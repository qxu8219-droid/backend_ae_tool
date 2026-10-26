import type { JsonValue } from "./generated/json-value.gen.js";
export interface paths {
    "/api/v1/robots": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Robots */
        get: operations["list_robots_api_v1_robots_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/robots/{robot_id}/joints": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Joints */
        get: operations["get_joints_api_v1_robots__robot_id__joints_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/scenes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Scenes */
        get: operations["list_scenes_api_v1_scenes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create Session
         * @description One session maximum. The runtime will asynchronously start IsaacSim 6.2.0/PhysX.
         */
        post: operations["create_session_api_v1_sessions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Session */
        get: operations["get_session_api_v1_sessions__sid__get"];
        put?: never;
        post?: never;
        /**
         * Delete Session
         * @description Idempotently destroy owned session resources and uploaded copies.
         */
        delete: operations["delete_session_api_v1_sessions__sid__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/logs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Upload Log */
        post: operations["upload_log_api_v1_sessions__sid__logs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/logs/{lid}/channels": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Channels */
        get: operations["get_channels_api_v1_sessions__sid__logs__lid__channels_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/logs/{lid}/curve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Curve */
        get: operations["get_curve_api_v1_sessions__sid__logs__lid__curve_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/playback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Playback
         * @description Server-observed target/applied/rendered state; no claim about browser display.
         */
        get: operations["get_playback_api_v1_sessions__sid__playback_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/playback/load": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Load Log */
        post: operations["load_log_api_v1_sessions__sid__playback_load_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/playback/pause": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Pause */
        post: operations["pause_api_v1_sessions__sid__playback_pause_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/playback/play": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Play */
        post: operations["play_api_v1_sessions__sid__playback_play_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/playback/seek": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Seek
         * @description Exactly one position. Runtime will clamp negative and beyond-end positions.
         */
        post: operations["seek_api_v1_sessions__sid__playback_seek_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/playback/speed": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Set Speed */
        post: operations["set_speed_api_v1_sessions__sid__playback_speed_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/playback/step": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Step */
        post: operations["step_api_v1_sessions__sid__playback_step_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/playback/stop": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Stop */
        post: operations["stop_api_v1_sessions__sid__playback_stop_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/sessions/{sid}/video": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Video */
        get: operations["get_video_api_v1_sessions__sid__video_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** Body_upload_log_api_v1_sessions__sid__logs_post */
        Body_upload_log_api_v1_sessions__sid__logs_post: {
            /**
             * File
             * @description Original log file, retained unchanged in the owned session archive.
             */
            file: string;
            /**
             * Mapping Config
             * @description Inline JSON object, not a file path. Stored with the original file as an immutable mapping snapshot.
             */
            mapping_config?: string | null;
            /** Parser */
            parser?: string | null;
        };
        /** ChannelDescriptor */
        ChannelDescriptor: {
            /**
             * Angle Semantics
             * @default linear
             * @enum {string}
             */
            angle_semantics?: "bounded" | "angular_wrapped" | "angular_unwrapped" | "linear";
            /** Joint Index */
            joint_index?: number | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "joint" | "chassis" | "custom" | "virtual";
            /** Label */
            label: string;
            /** Lower */
            lower?: number | null;
            /** Name */
            name: string;
            quality: components["schemas"]["ChannelQuality"];
            /** Source Field */
            source_field: string | null;
            /** Source Unit */
            source_unit?: string | null;
            /** Unit */
            unit: string;
            /** Upper */
            upper?: number | null;
            /** Wrap Period */
            wrap_period?: number | null;
        };
        /** ChannelQuality */
        ChannelQuality: {
            /** Method */
            method?: string | null;
            /**
             * Origin
             * @enum {string}
             */
            origin: "measured" | "commanded" | "derived" | "missing";
            /**
             * Processing
             * @default []
             */
            processing?: ("original" | "unit_converted" | "interpolated" | "forward_filled" | "defaulted" | "derived")[];
            /**
             * Source Indices
             * @default []
             */
            source_indices?: number[];
            /** Valid */
            valid: boolean;
        };
        /** ChassisAvailability */
        ChassisAvailability: {
            /** Available */
            available: boolean;
            /** Frame */
            frame?: "world" | null;
            /** Source Channels */
            source_channels?: string[];
        };
        /** ControlRequest */
        ControlRequest: {
            /**
             * Request Id
             * @description Opaque correlation ID; server creates one when omitted. Never compare IDs for ordering.
             */
            request_id?: string | null;
        };
        /** CreateSessionRequest */
        CreateSessionRequest: {
            /**
             * Isaacsim Version
             * @default 6.2.0
             * @constant
             */
            isaacsim_version?: "6.2.0";
            /**
             * Mode
             * @default kinematic
             * @constant
             */
            mode?: "kinematic";
            /**
             * Physics Engine
             * @default physx
             * @constant
             */
            physics_engine?: "physx";
            /** Robot Id */
            robot_id: string;
            /** Scene Id */
            scene_id: string;
        };
        /** CurvePoint */
        CurvePoint: {
            quality: components["schemas"]["ChannelQuality"];
            /** Time S */
            time_s: number;
            /** Value */
            value: number | null;
        };
        /** CurveResponse */
        CurveResponse: {
            /**
             * Downsampling
             * @default min_max
             * @constant
             */
            downsampling?: "min_max";
            /** From S */
            from_s: number;
            /** Log Id */
            log_id: string;
            /** Max Points */
            max_points: number;
            /** Series */
            series: components["schemas"]["CurveSeries"][];
            /** To S */
            to_s: number;
        };
        /** CurveSeries */
        CurveSeries: {
            /** Channel */
            channel: string;
            /** Limit Intervals */
            limit_intervals?: components["schemas"]["LimitInterval"][];
            /** Points */
            points: components["schemas"]["CurvePoint"][];
            /** Unit */
            unit: string;
        };
        /** ErrorDetail */
        ErrorDetail: {
            /** Code */
            code: string;
            /** Details */
            details?: {
                [key: string]: components["schemas"]["JsonValue"];
            };
            /** Message */
            message: string;
        };
        /** ErrorEnvelope */
        ErrorEnvelope: {
            error: components["schemas"]["ErrorDetail"];
        };
        /** FrameIdentity */
        FrameIdentity: {
            /** Capture Id */
            capture_id: number;
            /** Control Seq */
            control_seq: number;
            /** Log Id */
            log_id: string;
            /** Playback Epoch */
            playback_epoch: number;
            /** Render Seq */
            render_seq: number;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version?: 1;
            /** Session Id */
            session_id: string;
            /** Target Seq */
            target_seq: number;
        };
        /** IceServer */
        IceServer: {
            /** Credential */
            credential?: string | null;
            /** Urls */
            urls: [
                string,
                ...string[]
            ];
            /** Username */
            username?: string | null;
        };
        /** JointDescriptor */
        JointDescriptor: {
            /** Limits Provenance */
            limits_provenance: string;
            /** Lower */
            lower: number | null;
            /** Name */
            name: string;
            /**
             * Type
             * @enum {string}
             */
            type: "revolute" | "continuous" | "prismatic";
            /**
             * Unit
             * @enum {string}
             */
            unit: "rad" | "m";
            /** Upper */
            upper: number | null;
        };
        /** JointMetadata */
        JointMetadata: {
            /** Canonical Order */
            canonical_order: [
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string,
                string
            ];
            /** Joints */
            joints: [
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"],
                components["schemas"]["JointDescriptor"]
            ];
            /**
             * Limits Source
             * @description Authoritative versioned r1pro_2026_common.json path; limits retain per-joint provenance.
             */
            limits_source: string;
            /** Robot Id */
            robot_id: string;
        };
        JsonValue: JsonValue;
        /** LimitInterval */
        LimitInterval: {
            /** Channel */
            channel: string;
            /** Code */
            code: string;
            /** From S */
            from_s: number;
            /**
             * Severity
             * @enum {string}
             */
            severity: "warning" | "error";
            /** To S */
            to_s: number;
        };
        /** LoadRequest */
        LoadRequest: {
            /** Log Id */
            log_id: string;
            /**
             * Request Id
             * @description Opaque correlation ID; server creates one when omitted. Never compare IDs for ordering.
             */
            request_id?: string | null;
        };
        /** LogChannels */
        LogChannels: {
            /** Channels */
            channels: components["schemas"]["ChannelDescriptor"][];
            /** Log Id */
            log_id: string;
        };
        /** ParseIssue */
        ParseIssue: {
            /** Channel */
            channel?: string | null;
            /** Code */
            code: string;
            /** Details */
            details?: {
                [key: string]: components["schemas"]["JsonValue"];
            };
            /** Line */
            line?: number | null;
            /** Message */
            message: string;
            /**
             * Severity
             * @enum {string}
             */
            severity: "error" | "warning" | "info";
        };
        /** PlayRequest */
        PlayRequest: {
            /**
             * Request Id
             * @description Opaque correlation ID; server creates one when omitted. Never compare IDs for ordering.
             */
            request_id?: string | null;
            /** Speed */
            speed?: number | null;
        };
        /** PlaybackSnapshot */
        PlaybackSnapshot: {
            applied_position?: components["schemas"]["PositionRef"] | null;
            /** Control Seq */
            control_seq: number;
            /**
             * Duration S
             * @default 0
             */
            duration_s?: number;
            /** Log Id */
            log_id?: string | null;
            /** Playback Epoch */
            playback_epoch: number;
            rendered_identity?: components["schemas"]["FrameIdentity"] | null;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version?: 1;
            /** Session Id */
            session_id: string;
            /**
             * Speed
             * @default 1
             */
            speed?: number;
            /**
             * State
             * @enum {string}
             */
            state: "idle" | "ready" | "playing" | "paused" | "ended" | "error";
            target_position?: components["schemas"]["PositionRef"] | null;
        };
        /** PositionRef */
        PositionRef: {
            /** Log Time S */
            log_time_s: number;
            /** Source Sample Index */
            source_sample_index?: number | null;
            /** Target Seq */
            target_seq: number;
        };
        /**
         * RESTPlaybackResponse
         * @description Accepted control; server state is not evidence of browser presentation.
         */
        RESTPlaybackResponse: {
            /**
             * Ack Stage
             * @default accepted
             * @constant
             */
            ack_stage?: "accepted";
            /** Control Seq */
            control_seq: number;
            playback: components["schemas"]["PlaybackSnapshot"];
            /** Playback Epoch */
            playback_epoch: number;
            /** Request Id */
            request_id: string;
        };
        /** Robot */
        Robot: {
            /**
             * Dof
             * @default 28
             * @constant
             */
            dof?: 28;
            /** Id */
            id: string;
            /** Name */
            name: string;
            /** Usd Variants */
            usd_variants: [
                components["schemas"]["USDVariant"],
                ...components["schemas"]["USDVariant"][]
            ];
        };
        /** RobotList */
        RobotList: {
            /** Robots */
            robots: components["schemas"]["Robot"][];
        };
        /** Scene */
        Scene: {
            /** Description */
            description: string;
            /** Id */
            id: string;
            /** Name */
            name: string;
            /** Source */
            source: string;
            /** Thumbnail Url */
            thumbnail_url?: string | null;
        };
        /** SceneList */
        SceneList: {
            /** Scenes */
            scenes: components["schemas"]["Scene"][];
        };
        /** SeekRequest */
        SeekRequest: {
            /** Frame Index */
            frame_index?: number | null;
            /**
             * Request Id
             * @description Opaque correlation ID; server creates one when omitted. Never compare IDs for ordering.
             */
            request_id?: string | null;
            /** Time S */
            time_s?: number | null;
        } & ({
            frame_index?: never;
            time_s: number;
        } | {
            frame_index: number;
            time_s?: never;
        });
        /** SessionCreated */
        SessionCreated: {
            /** Session Id */
            session_id: string;
            /**
             * Status
             * @default starting
             * @constant
             */
            status?: "starting";
            /** Ws Url */
            ws_url: string;
        };
        /** SessionStatus */
        SessionStatus: {
            /**
             * Isaacsim Version
             * @constant
             */
            isaacsim_version: "6.2.0";
            /**
             * Mode
             * @default kinematic
             * @constant
             */
            mode?: "kinematic";
            /**
             * Physics Engine
             * @default physx
             * @constant
             */
            physics_engine?: "physx";
            /** Render Fps */
            render_fps: number | null;
            /** Robot Id */
            robot_id: string;
            /** Scene Id */
            scene_id: string;
            /** Session Id */
            session_id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "starting" | "ready" | "stopping" | "stopped" | "error";
            video: components["schemas"]["VideoInfo"];
            worker: components["schemas"]["WorkerStatus"];
            /** Ws Url */
            ws_url: string;
        };
        /** SpeedRequest */
        SpeedRequest: {
            /**
             * Request Id
             * @description Opaque correlation ID; server creates one when omitted. Never compare IDs for ordering.
             */
            request_id?: string | null;
            /** Speed */
            speed: number;
        };
        /** StepRequest */
        StepRequest: {
            /**
             * Direction
             * @enum {string}
             */
            direction: "forward" | "backward";
            /**
             * Request Id
             * @description Opaque correlation ID; server creates one when omitted. Never compare IDs for ordering.
             */
            request_id?: string | null;
        };
        /** USDVariant */
        USDVariant: {
            /**
             * Isaacsim Version
             * @constant
             */
            isaacsim_version: "6.2.0";
            /**
             * Physics Engine
             * @default physx
             * @constant
             */
            physics_engine?: "physx";
            /** Usd Path */
            usd_path: string;
        };
        /** UploadResult */
        UploadResult: {
            /** Channels */
            channels: components["schemas"]["ChannelDescriptor"][];
            /** Issues */
            issues: components["schemas"]["ParseIssue"][];
            /** Log Id */
            log_id: string;
            /** Mapping Snapshot */
            mapping_snapshot: {
                [key: string]: components["schemas"]["JsonValue"];
            };
            /** Mapping Snapshot Id */
            mapping_snapshot_id: string;
            /** Parser */
            parser: string;
            /** Raw File Ref */
            raw_file_ref: string;
            /** Raw File Sha256 */
            raw_file_sha256: string;
            stats: components["schemas"]["UploadStats"];
        };
        /** UploadStats */
        UploadStats: {
            chassis: components["schemas"]["ChassisAvailability"];
            /** Duration S */
            duration_s: number;
            /** Joints Mapped */
            joints_mapped: string[];
            /** Joints Missing */
            joints_missing: string[];
            /** Nominal Rate Hz */
            nominal_rate_hz: number | null;
            /** Sample Count */
            sample_count: number;
        };
        /** VideoInfo */
        VideoInfo: {
            /**
             * Catalog Schema Version
             * @default 1
             * @constant
             */
            catalog_schema_version?: 1;
            /**
             * Decoder
             * @default webcodecs_h264
             * @constant
             */
            decoder?: "webcodecs_h264";
            /** Ice Servers */
            ice_servers?: components["schemas"]["IceServer"][];
            /**
             * Identity Scheme
             * @default capture32_crc32
             * @constant
             */
            identity_scheme?: "capture32_crc32";
            /**
             * Presentation
             * @default verified_pair_single_canvas
             * @constant
             */
            presentation?: "verified_pair_single_canvas";
            /** Publishing */
            publishing: boolean;
            /**
             * Receiver Transform
             * @default rtcrtp_script_transform_or_encoded_streams
             * @constant
             */
            receiver_transform?: "rtcrtp_script_transform_or_encoded_streams";
            /** Render Fps */
            render_fps?: number | null;
            /** Sei Uuid Hex */
            sei_uuid_hex?: string | null;
            /**
             * Snapshot Transport
             * @default h264_sei_snapshot_catalog
             * @constant
             */
            snapshot_transport?: "h264_sei_snapshot_catalog";
            /** Stream Path */
            stream_path: string | null;
            /**
             * Transport
             * @default whep
             * @constant
             */
            transport?: "whep";
            /** Whep Url */
            whep_url: string | null;
        };
        /** WorkerStatus */
        WorkerStatus: {
            error?: components["schemas"]["ErrorDetail"] | null;
            /** Message */
            message?: string | null;
            /**
             * Phase
             * @enum {string}
             */
            phase: "booting" | "loading_scene" | "loading_robot" | "rtsp_publishing" | "ready" | "stopping" | "stopped" | "error";
            /** Pid */
            pid?: number | null;
            /**
             * Restart Policy
             * @default recreate_session_reload_from_start
             * @constant
             */
            restart_policy?: "recreate_session_reload_from_start";
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    list_robots_api_v1_robots_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RobotList"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    get_joints_api_v1_robots__robot_id__joints_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                robot_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["JointMetadata"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    list_scenes_api_v1_scenes_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SceneList"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    create_session_api_v1_sessions_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateSessionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionCreated"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    get_session_api_v1_sessions__sid__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionStatus"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    delete_session_api_v1_sessions__sid__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    upload_log_api_v1_sessions__sid__logs_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_upload_log_api_v1_sessions__sid__logs_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["UploadResult"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Upload exceeds the configured runtime size limit. */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Fatal log-content parse failure; error.details.issues contains ParseIssue records. Never request validation. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    get_channels_api_v1_sessions__sid__logs__lid__channels_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
                lid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LogChannels"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    get_curve_api_v1_sessions__sid__logs__lid__curve_get: {
        parameters: {
            query: {
                /** @description Comma-separated channel names; trim whitespace and deduplicate in first-occurrence order, 1..128 unique names. Empty entries are invalid. */
                channels: string;
                from_s?: number;
                to_s?: number | null;
                /** @description Per-channel point limit; min/max downsampling retains peaks. */
                max_points?: number;
            };
            header?: never;
            path: {
                sid: string;
                lid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CurveResponse"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    get_playback_api_v1_sessions__sid__playback_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlaybackSnapshot"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    load_log_api_v1_sessions__sid__playback_load_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoadRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RESTPlaybackResponse"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    pause_api_v1_sessions__sid__playback_pause_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ControlRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RESTPlaybackResponse"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    play_api_v1_sessions__sid__playback_play_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["PlayRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RESTPlaybackResponse"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    seek_api_v1_sessions__sid__playback_seek_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SeekRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RESTPlaybackResponse"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    set_speed_api_v1_sessions__sid__playback_speed_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SpeedRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RESTPlaybackResponse"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    step_api_v1_sessions__sid__playback_step_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["StepRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RESTPlaybackResponse"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    stop_api_v1_sessions__sid__playback_stop_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ControlRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RESTPlaybackResponse"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    get_video_api_v1_sessions__sid__video_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sid: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["VideoInfo"];
                };
            };
            /** @description Invalid request syntax, parameters, fields, or non-finite numbers. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Requested scene, robot, session, log, or channel does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description State conflict, including NO_LOG_LOADED or MAX_SESSIONS_REACHED. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Runtime is not configured, or the worker is unavailable/not ready. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description Other transport errors use the same error envelope. */
            default: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
}
