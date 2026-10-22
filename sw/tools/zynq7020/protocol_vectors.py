"""冻结无硬件协议样本；不是一整套真实机器运动报文。"""
import json
from pathlib import Path
from ec_26_e.motion.protocol import Servo42CProtocol
from ec_26_e.motion.contracts import MotorAddress
from ec_26_e.motion.f32c_controller import build_speed_frame, build_multi_turn_angle_frame

def main():
    vectors={}
    for axis in MotorAddress:
        vectors[f'{axis.name}_enable']=Servo42CProtocol.build_enable(axis,True).hex()
        vectors[f'{axis.name}_disable']=Servo42CProtocol.build_enable(axis,False).hex()
        vectors[f'{axis.name}_move_3200']=Servo42CProtocol.build_move(axis,64,3200).hex()
    vectors['rotation_speed_15']=build_speed_frame(15).hex()
    vectors['rotation_90']=build_multi_turn_angle_frame(90).hex()
    path=Path('../baselines/protocol_vectors.json');path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'kind':'protocol_vectors','motion_executed':False,'frames':vectors},indent=2))

if __name__=='__main__':main()
