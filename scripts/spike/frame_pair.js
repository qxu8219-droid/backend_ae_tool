// The image itself chooses its snapshot. Encoder poll order is never identity.
export function decodeCaptureStamp(samples) {
  if (!Array.isArray(samples) || samples.length !== 64) return {valid:false};
  const bytes = new Uint8Array(8);
  for (let bit=0;bit<64;bit++) {
    const pixel=samples[bit];
    if (!pixel || !Number.isFinite(pixel[0]) || !Number.isFinite(pixel[1]) ||
        Math.abs(pixel[1]-pixel[0])<30) return {valid:false};
    if (pixel[1]>pixel[0]) bytes[Math.floor(bit/8)] |= 1 << (bit%8);
  }
  const view=new DataView(bytes.buffer);
  const capture_id=view.getUint32(0,true), checksum=view.getUint32(4,true);
  let crc=0xffffffff;
  for (let shift=24;shift>=0;shift-=8) {
    crc^=(capture_id>>>shift)&255;
    for(let bit=0;bit<8;bit++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);
  }
  const expected_crc=(crc^0xffffffff)>>>0;
  return {capture_id,checksum,expected_crc,valid:capture_id>0&&checksum===expected_crc};
}

export function selectFrameSnapshot(envelope,samples) {
  const stamp=decodeCaptureStamp(samples);
  if(!stamp.valid)throw new Error('Invalid image capture stamp');
  const matches=envelope.snapshots.filter(snapshot=>snapshot.capture_id===stamp.capture_id);
  if(matches.length!==1||matches[0].session_id!==envelope.session_id)
    throw new Error('Image capture ID is absent or ambiguous in catalog');
  return {snapshot:matches[0],stamp};
}
