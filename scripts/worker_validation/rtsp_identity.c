/* T11 localhost RTSP receiver with independent pixel/SEI observation. Compile with build_rtsp_probe.sh.
 * No GPU decoder or encoder is requested. SECONDS is capture wall time after
 * the first valid frame; startup is limited to 5 s, frame inactivity to 2 s,
 * and the entire network operation to 5 s + SECONDS. */
#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/error.h>
#include <libavutil/imgutils.h>
#include <libavutil/time.h>
#include <libswscale/swscale.h>

enum StopReason { NONE, CAPTURE_COMPLETE, STARTUP_TIMEOUT, READ_TIMEOUT, TOTAL_TIMEOUT };
typedef struct {
    int64_t started, first, last, capture_end, total_end;
    int64_t frames, pts_frames, first_pts, last_pts, nonmonotonic_pts, sei_frames;
    double seconds;
    int width, height, dimension_changes, ppm_saved;
    enum StopReason stop;
    AVRational time_base, declared_rate;
    const char *codec;
    const char *ppm_path;
    char stage[48], error[256];
} Probe;


static FILE *identity_trace;
static void json_string(FILE *, const char *);
static uint32_t marker_crc(uint32_t value) {
    uint32_t crc=0xffffffffU;
    for (int byte=3;byte>=0;--byte) {
        crc ^= (value>>(byte*8))&255U;
        for(int bit=0;bit<8;++bit) crc=(crc>>1)^((crc&1U)?0xedb88320U:0);
    }
    return crc^0xffffffffU;
}
static int observe_identity(const AVFrame *frame) {
    struct SwsContext *sws=sws_getContext(frame->width,frame->height,(enum AVPixelFormat)frame->format,
        frame->width,frame->height,AV_PIX_FMT_RGB24,SWS_BILINEAR,NULL,NULL,NULL);
    uint8_t *rgb[4]={NULL};int stride[4]={0};
    if(!sws)return -1;
    if(av_image_alloc(rgb,stride,frame->width,frame->height,AV_PIX_FMT_RGB24,1)<0){sws_freeContext(sws);return -1;}
    sws_scale(sws,(const uint8_t *const *)frame->data,frame->linesize,0,frame->height,rgb,stride);
    uint32_t id=0,crc=0;int confident=1;
    for(int bit=0;bit<64;++bit) {
        double x=-.475+(bit%32)*.03;
        double y=(.32-(bit/32)*.04)*(double)frame->height/frame->width*4./3.;
        int px=(int)llround((x/(18./35.)+1.)*frame->width/2.);
        int py=(int)llround((1.-y/(18./35.*frame->height/frame->width))*frame->height/2.);
        if(px<0||px>=frame->width||py<0||py>=frame->height){confident=0;continue;}
        uint8_t *p=rgb[0]+py*stride[0]+px*3;
        if(abs((int)p[1]-(int)p[0])<30)confident=0;
        if(p[1]>p[0]) {if(bit<32)id|=1U<<bit;else crc|=1U<<(bit-32);}
    }
    fprintf(identity_trace,"{\"capture_id\":%"PRIu32",\"crc\":%"PRIu32",\"expected_crc\":%"PRIu32",\"confident\":%s,\"metadata_json\":",id,crc,marker_crc(id),confident?"true":"false");
    int found=0;char uuid[33]="";
    for(int i=0;i<frame->nb_side_data;++i) {
        AVFrameSideData *data=frame->side_data[i];
        if(data->type!=AV_FRAME_DATA_SEI_UNREGISTERED||data->size<=16||data->size>1048576||data->data[16]!='{')continue;
        char *payload=malloc(data->size-16+1);
        if(!payload)continue;
        memcpy(payload,data->data+16,data->size-16);payload[data->size-16]='\0';
        json_string(identity_trace,payload);free(payload);
        for(int b=0;b<16;++b)snprintf(uuid+b*2,3,"%02x",data->data[b]);
        found=1;break;
    }
    if(!found)fputs("null",identity_trace);
    fputs(",\"uuid_hex\":",identity_trace);json_string(identity_trace,uuid);fputs("}\n",identity_trace);fflush(identity_trace);
    av_freep(&rgb[0]);sws_freeContext(sws);return 0;
}
static void fail(Probe *p, const char *stage, const char *message, int code) {
    char detail[AV_ERROR_MAX_STRING_SIZE] = "";
    snprintf(p->stage, sizeof(p->stage), "%s", stage);
    if (code < 0) av_strerror(code, detail, sizeof(detail));
    snprintf(p->error, sizeof(p->error), "%s%s%s", message,
             code < 0 ? ": " : "", detail);
}

static int interrupt_io(void *opaque) {
    Probe *p = opaque;
    int64_t deadline = p->total_end;
    enum StopReason reason = TOTAL_TIMEOUT;
    int64_t startup_end = p->started + 5000000;
    if (!p->first && startup_end < deadline) {
        deadline = startup_end;
        reason = STARTUP_TIMEOUT;
    }
    if (p->first) {
        if (p->capture_end <= deadline) {
            deadline = p->capture_end;
            reason = CAPTURE_COMPLETE;
        }
        if (p->last + 2000000 < deadline) {
            deadline = p->last + 2000000;
            reason = READ_TIMEOUT;
        }
    }
    if (av_gettime_relative() >= deadline) {
        p->stop = reason;
        return 1;
    }
    return 0;
}

static int save_ppm(const AVFrame *frame, const char *path) {
    struct SwsContext *sws = sws_getContext(
        frame->width, frame->height, (enum AVPixelFormat)frame->format,
        frame->width, frame->height, AV_PIX_FMT_RGB24,
        SWS_BILINEAR, NULL, NULL, NULL);
    if (!sws) return AVERROR(ENOMEM);
    uint8_t *data[4] = {NULL};
    int linesize[4] = {0};
    int result = av_image_alloc(data, linesize, frame->width, frame->height, AV_PIX_FMT_RGB24, 1);
    if (result < 0) { sws_freeContext(sws); return result; }
    result = sws_scale(sws, (const uint8_t *const *)frame->data, frame->linesize,
                       0, frame->height, data, linesize);
    FILE *file = NULL;
    if (result != frame->height) {
        result = AVERROR(EIO);
    } else if (!(file = fopen(path, "wb"))) {
        result = AVERROR(errno);
    } else {
        result = 0;
        if (fprintf(file, "P6\n%d %d\n255\n", frame->width, frame->height) < 0)
            result = AVERROR(EIO);
        for (int y = 0; result == 0 && y < frame->height; ++y) {
            size_t bytes = (size_t)frame->width * 3;
            if (fwrite(data[0] + (size_t)y * (size_t)linesize[0], 1, bytes, file) != bytes)
                result = AVERROR(EIO);
        }
        if (fclose(file) != 0) result = AVERROR(EIO);
    }
    av_freep(&data[0]);
    sws_freeContext(sws);
    return result;
}

static int accept_frame(Probe *p, AVFrame *frame) {
    if (frame->width <= 0 || frame->height <= 0 || !frame->data[0] ||
        frame->decode_error_flags || (frame->flags & AV_FRAME_FLAG_CORRUPT)) {
        fail(p, "decode", "Decoder produced an invalid/corrupt frame", 0);
        return -1;
    }
    int64_t now = av_gettime_relative();
    if (!p->first) {
        p->first = now;
        p->capture_end = now + (int64_t)(p->seconds * 1000000.0);
        p->width = frame->width;
        p->height = frame->height;
        if (p->ppm_path) {
            int result = save_ppm(frame, p->ppm_path);
            if (result < 0) { fail(p, "save_ppm", "Cannot save first decoded frame", result); return -1; }
            p->ppm_saved = 1;
        }
    } else if (frame->width != p->width || frame->height != p->height) {
        ++p->dimension_changes;
        p->width = frame->width;
        p->height = frame->height;
    }
    if (observe_identity(frame)<0) { fail(p,"identity","Cannot read decoded pixels",0); return -1; }
    p->last = now;
    ++p->frames;
    int64_t pts = frame->best_effort_timestamp;
    if (pts != AV_NOPTS_VALUE) {
        if (!p->pts_frames) p->first_pts = pts;
        else if (pts <= p->last_pts) ++p->nonmonotonic_pts;
        p->last_pts = pts;
        ++p->pts_frames;
    }
    if (av_frame_get_side_data(frame, AV_FRAME_DATA_SEI_UNREGISTERED)) ++p->sei_frames;
    return 0;
}

static void json_string(FILE *out, const char *s) {
    fputc('"', out);
    for (const unsigned char *p = (const unsigned char *)(s ? s : ""); *p; ++p) {
        if (*p == '"' || *p == '\\') fprintf(out, "\\%c", *p);
        else if (*p < 32) fprintf(out, "\\u%04x", *p);
        else fputc(*p, out);
    }
    fputc('"', out);
}

static void json_number(FILE *out, double value) {
    if (isfinite(value)) fprintf(out, "%.9f", value);
    else fputs("null", out);
}

static const char *stop_name(enum StopReason reason) {
    switch (reason) {
        case CAPTURE_COMPLETE: return "capture_duration_complete";
        case STARTUP_TIMEOUT: return "startup_or_first_frame_timeout";
        case READ_TIMEOUT: return "decoded_frame_inactivity_timeout";
        case TOTAL_TIMEOUT: return "overall_timeout";
        case NONE: return "none";
    }
    return "unknown";
}

static int write_report(const Probe *p, const char *url, const char *path, int success) {
    FILE *out = fopen(path, "w");
    if (!out) { fprintf(stderr, "Cannot write JSON %s: %s\n", path, strerror(errno)); return -1; }
    double pts_span = p->pts_frames > 1 ? (double)(p->last_pts - p->first_pts) * av_q2d(p->time_base) : NAN;
    double wall_span = p->frames > 1 ? (double)(p->last - p->first) / 1e6 : NAN;
    fprintf(out, "{\n  \"status\": \"%s\",\n  \"url\": ", success ? "pass" : "fail");
    json_string(out, url);
    fputs(",\n  \"error_stage\": ", out); json_string(out, p->stage);
    fputs(",\n  \"error\": ", out); json_string(out, p->error);
    fputs(",\n  \"codec\": ", out); json_string(out, p->codec);
    fprintf(out, ",\n  \"transport\": \"tcp\",\n  \"decoder\": \"software\",\n"
            "  \"requested_capture_seconds\": %.6f,\n  \"startup_timeout_seconds\": 5,\n"
            "  \"read_inactivity_timeout_seconds\": 2,\n  \"stop_reason\": %d,\n"
            "  \"stop_reason_name\": \"%s\",\n"
            "  \"elapsed_wall_seconds\": %.6f,\n  \"valid_decoded_frames\": %" PRId64 ",\n"
            "  \"width\": %d,\n  \"height\": %d,\n  \"dimension_changes\": %d,\n"
            "  \"time_base\": [%d, %d],\n  \"declared_avg_frame_rate\": [%d, %d],\n"
            "  \"pts_samples\": %" PRId64 ",\n  \"nonmonotonic_pts\": %" PRId64 ",\n",
            p->seconds, (int)p->stop, stop_name(p->stop), (double)(av_gettime_relative()-p->started)/1e6,
            p->frames, p->width, p->height, p->dimension_changes,
            p->time_base.num, p->time_base.den, p->declared_rate.num, p->declared_rate.den,
            p->pts_frames, p->nonmonotonic_pts);
    fputs("  \"pts_source\": \"AVFrame.best_effort_timestamp in stream time_base\",\n  \"first_pts\": ", out);
    if (p->pts_frames) fprintf(out, "%" PRId64, p->first_pts); else fputs("null", out);
    fputs(",\n  \"last_pts\": ", out);
    if (p->pts_frames) fprintf(out, "%" PRId64, p->last_pts); else fputs("null", out);
    fputs(",\n  \"pts_span_seconds\": ", out); json_number(out, pts_span);
    fputs(",\n  \"pts_estimated_fps\": ", out);
    json_number(out, pts_span > 0 && !p->nonmonotonic_pts ? (double)(p->pts_frames-1)/pts_span : NAN);
    fputs(",\n  \"receive_wall_span_seconds\": ", out); json_number(out, wall_span);
    fputs(",\n  \"observed_receive_fps\": ", out);
    json_number(out, wall_span > 0 ? (double)(p->frames-1)/wall_span : NAN);
    fputs(",\n  \"startup_to_first_frame_seconds\": ", out);
    json_number(out, p->first ? (double)(p->first-p->started)/1e6 : NAN);
    fprintf(out, ",\n  \"frames_with_unregistered_sei\": %" PRId64 ",\n"
            "  \"first_frame_ppm_saved\": %s,\n  \"browser_frame_identity_verified\": false\n}\n",
            p->sei_frames, p->ppm_saved ? "true" : "false");
    int result = ferror(out) ? -1 : 0;
    if (fclose(out) != 0) result = -1;
    return result;
}

int main(int argc, char **argv) {
    if (argc != 4 && argc != 5) {
        fprintf(stderr, "Usage: %s RTSP_LOCALHOST_URL SECONDS OUTPUT_JSON [FIRST_FRAME_PPM]\n", argv[0]);
        return 2;
    }
    char trace_path[4096];
    if(snprintf(trace_path,sizeof(trace_path),"%s.frames.jsonl",argv[3])>=(int)sizeof(trace_path))return 2;
    identity_trace=fopen(trace_path,"w");if(!identity_trace)return 2;
    Probe p = {0};
    p.started = av_gettime_relative();
    p.codec = "";
    p.ppm_path = argc == 5 ? argv[4] : NULL;
    char *end = NULL;
    p.seconds = strtod(argv[2], &end);
    int success = 0, result = 0, video_index = -1;
    AVFormatContext *format = NULL;
    AVCodecContext *decoder = NULL;
    AVPacket *packet = NULL;
    AVFrame *frame = NULL;
    AVDictionary *options = NULL;
    if (!end || end == argv[2] || *end || !isfinite(p.seconds) || p.seconds <= 0 || p.seconds > 3600) {
        p.seconds = 0;
        fail(&p, "arguments", "SECONDS must be finite and in (0, 3600]", 0);
        goto done;
    }
    p.total_end = p.started + (int64_t)((p.seconds + 5.0) * 1000000.0);
    char scheme[32], auth[256], host[256];
    av_url_split(scheme, sizeof(scheme), auth, sizeof(auth), host, sizeof(host), NULL, NULL, 0, argv[1]);
    if (strcmp(scheme, "rtsp") || auth[0] ||
        (strcmp(host, "127.0.0.1") && strcmp(host, "localhost") && strcmp(host, "::1"))) {
        fail(&p, "arguments", "Only rtsp://localhost, 127.0.0.1 or [::1] without credentials is accepted", 0);
        goto done;
    }
    av_log_set_level(AV_LOG_WARNING);
    format = avformat_alloc_context();
    if (!format) { fail(&p, "allocate", "Cannot allocate demuxer", AVERROR(ENOMEM)); goto done; }
    format->interrupt_callback = (AVIOInterruptCB){ interrupt_io, &p };
    av_dict_set(&options, "rtsp_transport", "tcp", 0);
    av_dict_set(&options, "stimeout", "5000000", 0);
    av_dict_set(&options, "analyzeduration", "2000000", 0);
    result = avformat_open_input(&format, argv[1], NULL, &options);
    av_dict_free(&options);
    if (result < 0) { fail(&p, "connect", "Cannot open RTSP stream", result); goto done; }
    result = avformat_find_stream_info(format, NULL);
    if (result < 0) { fail(&p, "stream_info", "Cannot read stream information", result); goto done; }
    video_index = av_find_best_stream(format, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    if (video_index < 0) { fail(&p, "stream_info", "No video stream", video_index); goto done; }
    AVStream *stream = format->streams[video_index];
    const AVCodec *codec = avcodec_find_decoder(stream->codecpar->codec_id);
    p.codec = avcodec_get_name(stream->codecpar->codec_id);
    p.time_base = stream->time_base;
    p.declared_rate = stream->avg_frame_rate;
    if (!codec) { fail(&p, "decoder", "No software decoder for video codec", 0); goto done; }
    decoder = avcodec_alloc_context3(codec);
    if (!decoder) { fail(&p, "allocate", "Cannot allocate decoder", AVERROR(ENOMEM)); goto done; }
    result = avcodec_parameters_to_context(decoder, stream->codecpar);
    if (result < 0) { fail(&p, "decoder", "Cannot copy codec parameters", result); goto done; }
    decoder->thread_count = 1;
    decoder->pkt_timebase = stream->time_base;
    result = avcodec_open2(decoder, codec, NULL);
    if (result < 0) { fail(&p, "decoder", "Cannot open software decoder", result); goto done; }
    packet = av_packet_alloc();
    frame = av_frame_alloc();
    if (!packet || !frame) { fail(&p, "allocate", "Cannot allocate packet/frame", AVERROR(ENOMEM)); goto done; }
    while (!interrupt_io(&p)) {
        result = av_read_frame(format, packet);
        if (result < 0) {
            if (p.stop == CAPTURE_COMPLETE && p.frames) { success = 1; break; }
            fail(&p, "read", "RTSP read failed or timed out", result);
            goto done;
        }
        if (packet->stream_index != video_index) { av_packet_unref(packet); continue; }
        result = avcodec_send_packet(decoder, packet);
        av_packet_unref(packet);
        if (result < 0) { fail(&p, "decode", "Cannot submit packet", result); goto done; }
        while ((result = avcodec_receive_frame(decoder, frame)) >= 0) {
            if (accept_frame(&p, frame) < 0) goto done;
            av_frame_unref(frame);
        }
        if (result != AVERROR(EAGAIN) && result != AVERROR_EOF) {
            fail(&p, "decode", "Cannot decode video frame", result);
            goto done;
        }
    }
    if (p.stop == CAPTURE_COMPLETE && p.frames) success = 1;
    if (!success && !p.error[0]) fail(&p, "timeout", "Startup, frame inactivity or overall deadline reached", 0);
done:
    av_dict_free(&options);
    av_packet_free(&packet);
    av_frame_free(&frame);
    avcodec_free_context(&decoder);
    avformat_close_input(&format);
    fclose(identity_trace);
    if (write_report(&p, argv[1], argv[3], success) < 0) return 1;
    fprintf(stderr, "%s: frames=%" PRId64 " codec=%s size=%dx%d %s\n",
            success ? "PASS" : "FAIL", p.frames, p.codec, p.width, p.height, p.error);
    return success ? 0 : 1;
}
