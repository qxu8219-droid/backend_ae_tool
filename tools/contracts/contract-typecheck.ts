import type {components} from '../../contracts/types.gen.js';
import type {FrameSnapshot} from '../../contracts/generated/frame-catalog.gen.js';
import type {WsClientMessage, RpcResponse, PresentedFrame} from '../../contracts/messages.gen.js';

const joints:FrameSnapshot['joints']=[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0];
// @ts-expect-error The 28-joint vector must remain fixed length in generated types.
const shortJoints:FrameSnapshot['joints']=[0,0];
const seekTime:components['schemas']['SeekRequest']={time_s:1};
const seekFrame:components['schemas']['SeekRequest']={frame_index:3};
// @ts-expect-error A seek must identify a position.
const seekNeither:components['schemas']['SeekRequest']={};
// @ts-expect-error Time and frame positions are mutually exclusive.
const seekBoth:components['schemas']['SeekRequest']={time_s:1,frame_index:3};
type Rate=Extract<WsClientMessage,{type:'set_telemetry_rate'}>;
const validRate:Rate['data']['hz']=20;
// @ts-expect-error Health telemetry supports only the declared rates.
const invalidRate:Rate['data']['hz']=40;
// @ts-expect-error A server RPC may not claim a browser presentation.
const falseAcknowledgement:RpcResponse['stage']='presented';
const json:components['schemas']['JsonValue']={nested:[true,null,{value:1}]};
declare const shown:PresentedFrame;
const localGeneration:number=shown.connection_generation;
void [joints,shortJoints,seekTime,seekFrame,seekNeither,seekBoth,validRate,invalidRate,falseAcknowledgement,json,localGeneration];
