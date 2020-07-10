#!/usr/bin/env python3
# Software License Agreement (BSD License)
#
# Copyright (c) 2020, UFACTORY, Inc.
# All rights reserved.
#
# Author: Vinman <vinman.wen@ufactory.cc> <vinman.cub@gmail.com>

import os
import math
import time
import warnings
import threading
from collections import Iterable
from ..core.config.x_config import XCONF
from ..core.utils.log import logger
from .base import Base
from .gripper import Gripper
from .servo import Servo
from .record import Record
from .robotiq import RobotIQ
from .parse import GcodeParser
from .code import APIState
from .utils import xarm_is_connected, xarm_is_ready, xarm_is_pause, compare_version
try:
    from ..tools.blockly_tool import BlocklyTool
except:
    print('import BlocklyTool module failed')
    BlocklyTool = None

gcode_p = GcodeParser()


class XArm(Gripper, Servo, Record, RobotIQ):
    def __init__(self, port=None, is_radian=False, do_not_open=False, **kwargs):
        super(XArm, self).__init__()
        kwargs['init'] = True
        Base.__init__(self, port, is_radian, do_not_open, **kwargs)

    class _WaitMove:
        def __init__(self, owner, timeout):
            self.owner = owner
            # self.timeout = timeout if timeout is not None else 10
            self.timeout = timeout if timeout is not None else -1
            self.timer = None
            self.is_timeout = False

        def start(self):
            if self.timeout > 0:
                if self.owner._sleep_finish_time - time.time() > 0:
                    self.timeout += self.owner._sleep_finish_time - time.time()
                self.timer = threading.Timer(self.timeout, self.timeout_cb)
                self.timer.setDaemon(True)
                self.timer.start()
            self.check_stop_move()

        def check_stop_move(self):
            # base_joint_pos = self.owner.angles.copy()
            # time.sleep(0.1)
            count = 0
            while not self.is_timeout and not self.owner._is_stop and self.owner.connected and not self.owner.has_error:
                if self.owner.state in [4, 5]:
                    self.owner._sleep_finish_time = 0
                    break
                if time.time() < self.owner._sleep_finish_time:
                    time.sleep(0.02)
                    count = 0
                    continue
                if self.owner.state == 3:
                    time.sleep(0.02)
                    count = 0
                    continue
                # if self.owner.angles == base_joint_pos or self.owner.state != 1:
                if self.owner.state != 1:
                    count += 1
                    if count >= 10:
                        break
                    if count % 4 == 0:
                        self.owner.get_state()
                else:
                    # base_joint_pos = self.owner._angles.copy()
                    count = 0
                time.sleep(0.05)
            # if not self.is_timeout:
            #     self.owner._sync()

        def timeout_cb(self):
            self.is_timeout = True

    def _is_out_of_tcp_range(self, value, i):
        if not self._check_tcp_limit or self._stream_type != 'socket' or not self._enable_report:
            return False
        tcp_range = XCONF.Robot.TCP_LIMITS.get(self.axis).get(self.device_type, [])
        if 2 < i < len(tcp_range):  # only limit rotate
            limit = list(tcp_range[i])
            limit[0] += self._position_offset[i]
            limit[1] += self._position_offset[i]
            limit[0] += self._world_offset[i]
            limit[1] += self._world_offset[i]
            if limit[0] == limit[1]:
                return False
            if value < limit[0] - math.radians(0.1) or value > limit[1] + math.radians(0.1):
                logger.info('API -> set_position -> ret={}, i={} value={}'.format(APIState.OUT_OF_RANGE, i, value))
                return True
        return False

    def _is_out_of_joint_range(self, angle, i):
        if not self._check_joint_limit or self._stream_type != 'socket' or not self._enable_report:
            return False
        joint_limit = XCONF.Robot.JOINT_LIMITS.get(self.axis).get(self.device_type, [])
        if i < len(joint_limit):
            angle_range = joint_limit[i]
            if angle < angle_range[0] - math.radians(0.1) or angle > angle_range[1] + math.radians(0.1):
                logger.info('API -> set_servo_angle -> ret={}, i={} value={}'.format(APIState.OUT_OF_RANGE, i, angle))
                return True
        return False

    def _wait_until_cmdnum_lt_max(self):
        if not self._check_cmdnum_limit:
            return
        self._is_stop = False
        while self.cmd_num >= self._max_cmd_num:
            if not self.connected:
                return APIState.NOT_CONNECTED
            elif not self.ready:
                return APIState.NOT_READY
            elif self._is_stop:
                return APIState.EMERGENCY_STOP
            elif self.has_error:
                return
            time.sleep(0.1)

    @xarm_is_ready(_type='set')
    @xarm_is_pause(_type='set')
    def set_position(self, x=None, y=None, z=None, roll=None, pitch=None, yaw=None, radius=None,
                     speed=None, mvacc=None, mvtime=None, relative=False, is_radian=None,
                     wait=False, timeout=None, **kwargs):
        ret = self._wait_until_cmdnum_lt_max()
        if ret is not None:
            logger.info('API -> set_position -> ret={}'.format(ret))
            return ret

        is_radian = self._default_is_radian if is_radian is None else is_radian
        tcp_pos = [x, y, z, roll, pitch, yaw]
        last_used_position = self._last_position.copy()
        last_used_tcp_speed = self._last_tcp_speed
        last_used_tcp_acc = self._last_tcp_acc
        for i in range(6):
            value = tcp_pos[i]
            if value is None:
                continue
            elif isinstance(value, str):
                if value.isdigit():
                    value = float(value)
                else:
                    continue
            if relative:
                if 2 < i < 6:
                    if is_radian:
                        if self._is_out_of_tcp_range(self._last_position[i] + value, i):
                            self._last_position = last_used_position
                            return APIState.OUT_OF_RANGE
                        self._last_position[i] += value
                    else:
                        if self._is_out_of_tcp_range(self._last_position[i] + math.radians(value), i):
                            self._last_position = last_used_position
                            return APIState.OUT_OF_RANGE
                        self._last_position[i] += math.radians(value)
                else:
                    self._last_position[i] += value
            else:
                if 2 < i < 6:
                    if is_radian:
                        if self._is_out_of_tcp_range(value, i):
                            self._last_position = last_used_position
                            return APIState.OUT_OF_RANGE
                        self._last_position[i] = value
                    else:
                        if self._is_out_of_tcp_range(math.radians(value), i):
                            self._last_position = last_used_position
                            return APIState.OUT_OF_RANGE
                        self._last_position[i] = math.radians(value)
                else:
                    self._last_position[i] = value

        if speed is not None:
            if isinstance(speed, str):
                if speed.isdigit():
                    speed = float(speed)
                else:
                    speed = self._last_tcp_speed
            self._last_tcp_speed = min(max(speed, self._min_tcp_speed), self._max_tcp_speed)
        elif kwargs.get('mvvelo', None) is not None:
            mvvelo = kwargs.get('mvvelo')
            if isinstance(mvvelo, str):
                if mvvelo.isdigit():
                    mvvelo = float(mvvelo)
                else:
                    mvvelo = self._last_tcp_speed
            self._last_tcp_speed = min(max(mvvelo, self._min_tcp_speed), self._max_tcp_speed)
        if mvacc is not None:
            if isinstance(mvacc, str):
                if mvacc.isdigit():
                    mvacc = float(mvacc)
                else:
                    mvacc = self._last_tcp_acc
            self._last_tcp_acc = min(max(mvacc, self._min_tcp_acc), self._max_tcp_acc)
        if mvtime is not None:
            if isinstance(mvtime, str):
                if mvacc.isdigit():
                    mvtime = float(mvtime)
                else:
                    mvtime = self._mvtime
            self._mvtime = mvtime

        if kwargs.get('check', False):
            _, limit = self.is_tcp_limit(self._last_position)
            if _ == 0 and limit is True:
                self._last_position = last_used_position
                self._last_tcp_speed = last_used_tcp_speed
                self._last_tcp_acc = last_used_tcp_acc
                return APIState.TCP_LIMIT
        if radius is not None and radius >= 0:
            ret = self.arm_cmd.move_lineb(self._last_position, self._last_tcp_speed, self._last_tcp_acc, self._mvtime, radius)
        else:
            ret = self.arm_cmd.move_line(self._last_position, self._last_tcp_speed, self._last_tcp_acc, self._mvtime)
        logger.info('API -> set_position -> ret={}, pos={}, radius={}, velo={}, acc={}'.format(
            ret[0], self._last_position, radius, self._last_tcp_speed, self._last_tcp_acc
        ))
        self._is_set_move = True
        if wait and ret[0] in [0, XCONF.UxbusState.WAR_CODE, XCONF.UxbusState.ERR_CODE]:
            if not self._enable_report:
                warnings.warn('if you want to wait, please enable report')
            else:
                self._is_stop = False
                self._WaitMove(self, timeout).start()
                self._is_stop = False
                return APIState.HAS_ERROR if self.error_code != 0 else APIState.HAS_WARN if self.warn_code != 0 else APIState.NORMAL
        if ret[0] < 0 and not self.get_is_moving():
            self._last_position = last_used_position
            self._last_tcp_speed = last_used_tcp_speed
            self._last_tcp_acc = last_used_tcp_acc
        return ret[0]

    @xarm_is_ready(_type='set')
    @xarm_is_pause(_type='set')
    def set_tool_position(self, x=0, y=0, z=0, roll=0, pitch=0, yaw=0,
                          speed=None, mvacc=None, mvtime=None, is_radian=None,
                          wait=False, timeout=None, **kwargs):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        last_used_tcp_speed = self._last_tcp_speed
        last_used_tcp_acc = self._last_tcp_acc
        mvpose = [x, y, z, roll, pitch, yaw]
        if not is_radian:
            mvpose = [x, y, z, math.radians(roll), math.radians(pitch), math.radians(yaw)]
        if speed is not None:
            if isinstance(speed, str):
                if speed.isdigit():
                    speed = float(speed)
                else:
                    speed = self._last_tcp_speed
            self._last_tcp_speed = min(max(speed, self._min_tcp_speed), self._max_tcp_speed)
        elif kwargs.get('mvvelo', None) is not None:
            mvvelo = kwargs.get('mvvelo')
            if isinstance(mvvelo, str):
                if mvvelo.isdigit():
                    mvvelo = float(mvvelo)
                else:
                    mvvelo = self._last_tcp_speed
            self._last_tcp_speed = min(max(mvvelo, self._min_tcp_speed), self._max_tcp_speed)
        if mvacc is not None:
            if isinstance(mvacc, str):
                if mvacc.isdigit():
                    mvacc = float(mvacc)
                else:
                    mvacc = self._last_tcp_acc
            self._last_tcp_acc = min(max(mvacc, self._min_tcp_acc), self._max_tcp_acc)
        if mvtime is not None:
            if isinstance(mvtime, str):
                if mvacc.isdigit():
                    mvtime = float(mvtime)
                else:
                    mvtime = self._mvtime
            self._mvtime = mvtime

        ret = self.arm_cmd.move_line_tool(mvpose, self._last_tcp_speed, self._last_tcp_acc, self._mvtime)
        logger.info('API -> set_tool_position -> ret={}, pos={}, velo={}, acc={}'.format(
            ret[0], mvpose, self._last_tcp_speed, self._last_tcp_acc
        ))
        self._is_set_move = True
        if wait and ret[0] in [0, XCONF.UxbusState.WAR_CODE, XCONF.UxbusState.ERR_CODE]:
            if not self._enable_report:
                warnings.warn('if you want to wait, please enable report')
            else:
                self._is_stop = False
                self._WaitMove(self, timeout).start()
                self._is_stop = False
                return APIState.HAS_ERROR if self.error_code != 0 else APIState.HAS_WARN if self.warn_code != 0 else APIState.NORMAL
        if ret[0] < 0 and not self.get_is_moving():
            self._last_tcp_speed = last_used_tcp_speed
            self._last_tcp_acc = last_used_tcp_acc
        return ret[0]

    @xarm_is_ready(_type='set')
    @xarm_is_pause(_type='set')
    def set_position_aa(self, mvpose, speed=None, mvacc=None, mvtime=None,
                        is_radian=None, is_tool_coord=False, relative=False,
                        wait=False, timeout=None, **kwargs):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        last_used_tcp_speed = self._last_tcp_speed
        last_used_tcp_acc = self._last_tcp_acc
        pose = [mvpose[i] if i <= 2 or is_radian else math.radians(mvpose[i]) for i in range(6)]
        if speed is not None:
            if isinstance(speed, str):
                if speed.isdigit():
                    speed = float(speed)
                else:
                    speed = self._last_tcp_speed
            self._last_tcp_speed = min(max(speed, self._min_tcp_speed), self._max_tcp_speed)
        if mvacc is not None:
            if isinstance(mvacc, str):
                if mvacc.isdigit():
                    mvacc = float(mvacc)
                else:
                    mvacc = self._last_tcp_acc
            self._last_tcp_acc = min(max(mvacc, self._min_tcp_acc), self._max_tcp_acc)
        if mvtime is not None:
            if isinstance(mvtime, str):
                if mvacc.isdigit():
                    mvtime = float(mvtime)
                else:
                    mvtime = self._mvtime
            self._mvtime = mvtime

        mvcoord = kwargs.get('mvcoord', int(is_tool_coord))

        ret = self.arm_cmd.move_line_aa(pose, self._last_tcp_speed, self._last_tcp_acc, self._mvtime, mvcoord, int(relative))
        logger.info('API -> set_position_aa -> ret={}, pos={}, velo={}, acc={}'.format(
            ret[0], pose, self._last_tcp_speed, self._last_tcp_acc
        ))
        self._is_set_move = True
        if wait and ret[0] in [0, XCONF.UxbusState.WAR_CODE, XCONF.UxbusState.ERR_CODE]:
            if not self._enable_report:
                warnings.warn('if you want to wait, please enable report')
            else:
                self._is_stop = False
                self._WaitMove(self, timeout).start()
                self._is_stop = False
                return APIState.HAS_ERROR if self.error_code != 0 else APIState.HAS_WARN if self.warn_code != 0 else APIState.NORMAL
        if ret[0] < 0 and not self.get_is_moving():
            self._last_tcp_speed = last_used_tcp_speed
            self._last_tcp_acc = last_used_tcp_acc
        return ret[0]

    @xarm_is_ready(_type='set')
    @xarm_is_pause(_type='set')
    def set_servo_cartesian_aa(self, mvpose, speed=None, mvacc=None, is_radian=None, is_tool_coord=False, relative=False, **kwargs):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        assert len(mvpose) >= 6

        pose = [mvpose[i] if i <= 2 or is_radian else math.radians(mvpose[i]) for i in range(6)]
        _speed = self.last_used_tcp_speed if speed is None else speed
        _mvacc = self.last_used_tcp_acc if mvacc is None else mvacc

        tool_coord = kwargs.get('tool_coord', int(is_tool_coord))

        ret = self.arm_cmd.move_servo_cart_aa(mvpose=pose, mvvelo=_speed, mvacc=_mvacc, tool_coord=tool_coord,
                                              relative=int(relative))
        logger.info('API -> set_servo_cartesian_aa -> ret={}, pose={}, velo={}, acc={}'.format(
            ret[0], pose, _speed, _mvacc
        ))
        self._is_set_move = True
        return ret[0]

    @xarm_is_ready(_type='set')
    @xarm_is_pause(_type='set')
    def set_servo_angle(self, servo_id=None, angle=None, speed=None, mvacc=None, mvtime=None,
                        relative=False, is_radian=None, wait=False, timeout=None, **kwargs):
        assert ((servo_id is None or servo_id == 8) and isinstance(angle, Iterable)) \
            or (1 <= servo_id <= 7 and angle is not None and not isinstance(angle, Iterable)), \
            'param servo_id or angle error'
        ret = self._wait_until_cmdnum_lt_max()
        if ret is not None:
            logger.info('API -> set_servo_angle -> ret={}'.format(ret))
            return ret

        last_used_angle = self._last_angles.copy()
        last_used_joint_speed = self._last_joint_speed
        last_used_joint_acc = self._last_joint_acc
        is_radian = self._default_is_radian if is_radian is None else is_radian
        if servo_id is None or servo_id == 8:
            for i in range(min(len(angle), len(self._last_angles))):
                value = angle[i]
                if value is None or i >= self.axis:
                    continue
                if isinstance(value, str):
                    if value.isdigit():
                        value = float(value)
                    else:
                        continue
                if relative:
                    if is_radian:
                        if self._is_out_of_joint_range(self._last_angles[i] + value, i):
                            self._last_angles = last_used_angle
                            return APIState.OUT_OF_RANGE
                        self._last_angles[i] += value
                    else:
                        if self._is_out_of_joint_range(self._last_angles[i] + math.radians(value), i):
                            self._last_angles = last_used_angle
                            return APIState.OUT_OF_RANGE
                        self._last_angles[i] += math.radians(value)
                else:
                    if is_radian:
                        if self._is_out_of_joint_range(value, i):
                            self._last_angles = last_used_angle
                            return APIState.OUT_OF_RANGE
                        self._last_angles[i] = value
                    else:
                        if self._is_out_of_joint_range(math.radians(value), i):
                            self._last_angles = last_used_angle
                            return APIState.OUT_OF_RANGE
                        self._last_angles[i] = math.radians(value)
        else:
            if servo_id > self.axis:
                return APIState.SERVO_NOT_EXIST
            if isinstance(angle, str):
                if angle.isdigit():
                    angle = float(angle)
                else:
                    raise Exception('param angle error')
            if relative:
                if is_radian:
                    if self._is_out_of_joint_range(self._last_angles[servo_id - 1] + angle, servo_id - 1):
                        self._last_angles = last_used_angle
                        return APIState.OUT_OF_RANGE
                    self._last_angles[servo_id - 1] += angle
                else:
                    if self._is_out_of_joint_range(self._last_angles[servo_id - 1] + math.radians(angle), servo_id - 1):
                        self._last_angles = last_used_angle
                        return APIState.OUT_OF_RANGE
                    self._last_angles[servo_id - 1] += math.radians(angle)
            else:
                if is_radian:
                    if self._is_out_of_joint_range(angle, servo_id - 1):
                        self._last_angles = last_used_angle
                        return APIState.OUT_OF_RANGE
                    self._last_angles[servo_id - 1] = angle
                else:
                    if self._is_out_of_joint_range(math.radians(angle), servo_id - 1):
                        self._last_angles = last_used_angle
                        return APIState.OUT_OF_RANGE
                    self._last_angles[servo_id - 1] = math.radians(angle)

        if speed is not None:
            if isinstance(speed, str):
                if speed.isdigit():
                    speed = float(speed)
                else:
                    speed = self._last_joint_speed if is_radian else math.degrees(self._last_joint_speed)
            if not is_radian:
                speed = math.radians(speed)
            self._last_joint_speed = min(max(speed, self._min_joint_speed), self._max_joint_speed)
        elif kwargs.get('mvvelo', None) is not None:
            mvvelo = kwargs.get('mvvelo')
            if isinstance(mvvelo, str):
                if mvvelo.isdigit():
                    mvvelo = float(mvvelo)
                else:
                    mvvelo = self._last_joint_speed if is_radian else math.degrees(self._last_joint_speed)
            if not is_radian:
                mvvelo = math.radians(mvvelo)
            self._last_joint_speed = min(max(mvvelo, self._min_joint_speed), self._max_joint_speed)
        if mvacc is not None:
            if isinstance(mvacc, str):
                if mvacc.isdigit():
                    mvacc = float(mvacc)
                else:
                    mvacc = self._last_joint_acc if is_radian else math.degrees(self._last_joint_acc)
            if not is_radian:
                mvacc = math.radians(mvacc)
            self._last_joint_acc = min(max(mvacc, self._min_joint_acc), self._max_joint_acc)
        if mvtime is not None:
            if isinstance(mvtime, str):
                if mvacc.isdigit():
                    mvtime = float(mvtime)
                else:
                    mvtime = self._mvtime
            self._mvtime = mvtime

        if kwargs.get('check', False):
            _, limit = self.is_joint_limit(self._last_angles)
            if _ == 0 and limit is True:
                self._last_angles = last_used_angle
                self._last_joint_speed = last_used_joint_speed
                self._last_joint_acc = last_used_joint_acc
                return APIState.JOINT_LIMIT

        ret = self.arm_cmd.move_joint(self._last_angles, self._last_joint_speed, self._last_joint_acc, self._mvtime)
        logger.info('API -> set_servo_angle -> ret={}, angles={}, velo={}, acc={}'.format(
            ret[0], self._last_angles, self._last_joint_speed, self._last_joint_acc
        ))
        self._is_set_move = True
        if wait and ret[0] in [0, XCONF.UxbusState.WAR_CODE, XCONF.UxbusState.ERR_CODE]:
            if not self._enable_report:
                warnings.warn('if you want to wait, please enable report')
            else:
                self._is_stop = False
                self._WaitMove(self, timeout).start()
                self._is_stop = False
                return APIState.HAS_ERROR if self.error_code != 0 else APIState.HAS_WARN if self.warn_code != 0 else APIState.NORMAL
        if ret[0] < 0 and not self.get_is_moving():
            self._last_angles = last_used_angle
            self._last_joint_speed = last_used_joint_speed
            self._last_joint_acc = last_used_joint_acc
        return ret[0]

    @xarm_is_ready(_type='set')
    def set_servo_angle_j(self, angles, speed=None, mvacc=None, mvtime=None, is_radian=None, **kwargs):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        _angles = [angle if is_radian else math.radians(angle) for angle in angles]
        for i in range(self.axis):
            if self._is_out_of_joint_range(_angles[i], i):
                return APIState.OUT_OF_RANGE
        while len(_angles) < 7:
            _angles.append(0)
        _speed = self._last_joint_speed if speed is None else speed
        _mvacc = self._last_joint_acc if mvacc is None else mvacc
        _mvtime = self._mvtime if mvtime is None else mvtime
        ret = self.arm_cmd.move_servoj(_angles, _speed, _mvacc, _mvtime)
        logger.info('API -> set_servo_angle_j -> ret={}, angles={}, velo={}, acc={}'.format(
            ret[0], _angles, _speed, _mvacc
        ))
        self._is_set_move = True
        return ret[0]

    @xarm_is_ready(_type='set')
    def set_servo_cartesian(self, mvpose, speed=None, mvacc=None, mvtime=None, is_radian=None, is_tool_coord=False, **kwargs):
        assert len(mvpose) >= 6
        is_radian = self._default_is_radian if is_radian is None else is_radian
        if not is_radian:
            pose = [mvpose[i] if i < 3 else math.radians(mvpose[i]) for i in range(6)]
        else:
            pose = mvpose
        _speed = self.last_used_tcp_speed if speed is None else speed
        _mvacc = self.last_used_tcp_acc if mvacc is None else mvacc
        # _mvtime = self._mvtime if mvtime is None else mvtime
        _mvtime = int(is_tool_coord)

        ret = self.arm_cmd.move_servo_cartesian(pose, _speed, _mvacc, _mvtime)
        logger.info('API -> set_servo_cartisian -> ret={}, pose={}, velo={}, acc={}'.format(
            ret[0], pose, _speed, _mvacc
        ))
        self._is_set_move = True
        return ret[0]

    @xarm_is_ready(_type='set')
    @xarm_is_pause(_type='set')
    def move_circle(self, pose1, pose2, percent, speed=None, mvacc=None, mvtime=None, is_radian=None, wait=False, timeout=None, **kwargs):
        ret = self._wait_until_cmdnum_lt_max()
        if ret is not None:
            logger.info('API -> move_circle -> ret={}'.format(ret))
            return ret
        last_used_tcp_speed = self._last_tcp_speed
        last_used_tcp_acc = self._last_tcp_acc
        is_radian = self._default_is_radian if is_radian is None else is_radian
        pose_1 = []
        pose_2 = []
        for i in range(6):
            pose_1.append(pose1[i] if i < 3 or is_radian else math.radians(pose1[i]))
            pose_2.append(pose2[i] if i < 3 or is_radian else math.radians(pose2[i]))
        if speed is not None:
            if isinstance(speed, str):
                if speed.isdigit():
                    speed = float(speed)
                else:
                    speed = self._last_tcp_speed
            self._last_tcp_speed = min(max(speed, self._min_tcp_speed), self._max_tcp_speed)
        elif kwargs.get('mvvelo', None) is not None:
            mvvelo = kwargs.get('mvvelo')
            if isinstance(mvvelo, str):
                if mvvelo.isdigit():
                    mvvelo = float(mvvelo)
                else:
                    mvvelo = self._last_tcp_speed
            self._last_tcp_speed = min(max(mvvelo, self._min_tcp_speed), self._max_tcp_speed)
        if mvacc is not None:
            if isinstance(mvacc, str):
                if mvacc.isdigit():
                    mvacc = float(mvacc)
                else:
                    mvacc = self._last_tcp_acc
            self._last_tcp_acc = min(max(mvacc, self._min_tcp_acc), self._max_tcp_acc)
        if mvtime is not None:
            if isinstance(mvtime, str):
                if mvacc.isdigit():
                    mvtime = float(mvtime)
                else:
                    mvtime = self._mvtime
            self._mvtime = mvtime

        ret = self.arm_cmd.move_circle(pose_1, pose_2, self._last_tcp_speed, self._last_tcp_acc, self._mvtime, percent)
        logger.info('API -> move_circle -> ret={}, pos1={}, pos2={}, percent={}%, velo={}, acc={}'.format(
            ret[0], pose_1, pose_2, percent, self._last_tcp_speed, self._last_tcp_acc
        ))
        self._is_set_move = True

        if wait and ret[0] in [0, XCONF.UxbusState.WAR_CODE, XCONF.UxbusState.ERR_CODE]:
            if not self._enable_report:
                print('if you want to wait, please enable report')
            else:
                self._is_stop = False
                self._WaitMove(self, timeout).start()
                self._is_stop = False
                return APIState.HAS_ERROR if self.error_code != 0 else APIState.HAS_WARN if self.warn_code != 0 else APIState.NORMAL
        if ret[0] < 0 and not self.get_is_moving():
            self._last_tcp_speed = last_used_tcp_speed
            self._last_tcp_acc = last_used_tcp_acc
        return ret[0]

    @xarm_is_ready(_type='set')
    @xarm_is_pause(_type='set')
    def move_gohome(self, speed=None, mvacc=None, mvtime=None, is_radian=None, wait=False, timeout=None, **kwargs):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        # if speed is None:
        #     speed = 0.8726646259971648  # 50 °/s
        # else:
        #     if not is_radian:
        #         speed = math.radians(speed)
        # if mvacc is None:
        #     mvacc = 17.453292519943297  # 1000 °/s^2
        # else:
        #     if not is_radian:
        #         mvacc = math.radians(mvacc)
        # if mvtime is None:
        #     mvtime = 0

        if speed is not None:
            if isinstance(speed, str):
                if speed.isdigit():
                    speed = float(speed)
                else:
                    speed = self._last_joint_speed if is_radian else math.degrees(self._last_joint_speed)
            if not is_radian:
                speed = math.radians(speed)
            self._last_joint_speed = min(max(speed, self._min_joint_speed), self._max_joint_speed)
        elif kwargs.get('mvvelo', None) is not None:
            mvvelo = kwargs.get('mvvelo')
            if isinstance(mvvelo, str):
                if mvvelo.isdigit():
                    mvvelo = float(mvvelo)
                else:
                    mvvelo = self._last_joint_speed if is_radian else math.degrees(self._last_joint_speed)
            if not is_radian:
                mvvelo = math.radians(mvvelo)
            self._last_joint_speed = min(max(mvvelo, self._min_joint_speed), self._max_joint_speed)
        if mvacc is not None:
            if isinstance(mvacc, str):
                if mvacc.isdigit():
                    mvacc = float(mvacc)
                else:
                    mvacc = self._last_joint_acc if is_radian else math.degrees(self._last_joint_acc)
            if not is_radian:
                mvacc = math.radians(mvacc)
            self._last_joint_acc = min(max(mvacc, self._min_joint_acc), self._max_joint_acc)
        if mvtime is not None:
            if isinstance(mvtime, str):
                if mvacc.isdigit():
                    mvtime = float(mvtime)
                else:
                    mvtime = self._mvtime
            self._mvtime = mvtime

        ret = self.arm_cmd.move_gohome(self._last_joint_speed, self._last_joint_acc, self._mvtime)
        logger.info('API -> move_gohome -> ret={}, velo={}, acc={}'.format(
            ret[0], self._last_joint_speed, self._last_joint_acc
        ))
        self._is_set_move = True
        if ret[0] in [0, XCONF.UxbusState.WAR_CODE, XCONF.UxbusState.ERR_CODE]:
            pass
            # self._last_position = [201.5, 0, 140.5, -3.1415926, 0, 0]
            # self._last_angles = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        if wait and ret[0] in [0, XCONF.UxbusState.WAR_CODE, XCONF.UxbusState.ERR_CODE]:
            if not self._enable_report:
                warnings.warn('if you want to wait, please enable report')
            else:
                self._is_stop = False
                self._WaitMove(self, timeout).start()
                self._is_stop = False
                return APIState.HAS_ERROR if self.error_code != 0 else APIState.HAS_WARN if self.warn_code != 0 else APIState.NORMAL
        return ret[0]

    @xarm_is_ready(_type='set')
    def move_arc_lines(self, paths, is_radian=None, times=1, first_pause_time=0.1, repeat_pause_time=0,
                       automatic_calibration=True, speed=None, mvacc=None, mvtime=None, wait=False):
        assert len(paths) > 0, 'parameter paths error'
        is_radian = self._default_is_radian if is_radian is None else is_radian
        if speed is None:
            speed = self._last_tcp_speed
        if mvacc is None:
            mvacc = self._last_tcp_acc
        if mvtime is None:
            mvtime = 0
        logger.info('move_arc_lines--begin')
        if automatic_calibration:
            _ = self.set_position(*paths[0], is_radian=is_radian, speed=speed, mvacc=mvacc, mvtime=mvtime, wait=True)
            if _ < 0:
                logger.error('quit, api failed, code={}'.format(_))
                return
            _, angles = self.get_servo_angle(is_radian=True)
        self.set_pause_time(first_pause_time)
        self._is_stop = False
        last_used_joint_speed = self._last_joint_speed

        def _move():
            if automatic_calibration:
                ret = self.set_servo_angle(angle=angles, is_radian=True, speed=0.8726646259971648, wait=False)
                if ret < 0:
                    return -1
                self._last_joint_speed = last_used_joint_speed
            for path in paths:
                if len(path) > 6 and path[6] >= 0:
                    radius = path[6]
                else:
                    radius = 0
                if self.has_error or self._is_stop:
                    return -2
                ret = self.set_position(*path[:6], radius=radius, is_radian=is_radian, wait=False, speed=speed, mvacc=mvacc, mvtime=mvtime)
                if ret < 0:
                    return -1
            return 0
        count = 1
        api_failed = False

        def state_changed_callback(item):
            if item['state'] == 4:
                self._is_stop = True

        self.register_state_changed_callback(state_changed_callback)
        try:
            if times == 0:
                while not self.has_error and not self._is_stop:
                    _ = _move()
                    if _ == -1:
                        api_failed = True
                        break
                    elif _ == -2:
                        break
                    count += 1
                    self.set_pause_time(repeat_pause_time)
                if api_failed:
                    logger.error('quit, api error')
                elif self._error_code != 0:
                    logger.error('quit, controller error')
                elif self._is_stop:
                    logger.error('quit, emergency_stop')
            else:
                for i in range(times):
                    if self.has_error or self._is_stop:
                        break
                    _ = _move()
                    if _ == -1:
                        api_failed = True
                        break
                    elif _ == -2:
                        break
                    count += 1
                    self.set_pause_time(repeat_pause_time)
                if api_failed:
                    logger.error('quit, api error')
                elif self._error_code != 0:
                    logger.error('quit, controller error')
                elif self._is_stop:
                    logger.error('quit, emergency_stop')
        except:
            pass
        finally:
            self.release_state_changed_callback(state_changed_callback)
        logger.info('move_arc_lines--end')
        if wait:
            self._WaitMove(self, 0).start()
        self._is_stop = False

    @xarm_is_connected(_type='set')
    def set_servo_attach(self, servo_id=None):
        # assert isinstance(servo_id, int) and 1 <= servo_id <= 8
        # ret = self.arm_cmd.set_brake(servo_id, 0)
        logger.info('set_servo_attach--begin')
        ret = self.motion_enable(servo_id=servo_id, enable=True)
        self.set_state(0)
        self._sync()
        logger.info('set_servo_attach--end')
        return ret

    @xarm_is_connected(_type='set')
    def set_servo_detach(self, servo_id=None):
        """
        :param servo_id: 1-7, 8
        :return: 
        """
        assert isinstance(servo_id, int) and 1 <= servo_id <= 8, 'The value of parameter servo_id can only be 1-8.'
        ret = self.arm_cmd.set_brake(servo_id, 1)
        logger.info('API -> set_servo_detach -> ret={}'.format(ret[0]))
        self._sync()
        return ret[0]

    @xarm_is_connected(_type='set')
    def shutdown_system(self, value=1):
        ret = self.arm_cmd.shutdown_system(value)
        logger.info('API -> shutdown_system -> ret={}'.format(ret[0]))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_reduced_mode(self, on_off):
        ret = self.arm_cmd.set_reduced_mode(on_off)
        logger.info('API -> set_reduced_mode -> ret={}'.format(ret[0]))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_reduced_max_tcp_speed(self, speed):
        ret = self.arm_cmd.set_reduced_linespeed(speed)
        logger.info('API -> set_reduced_linespeed -> ret={}, speed={}'.format(ret[0], speed))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_reduced_max_joint_speed(self, speed, is_radian=None):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        speed = speed
        if not is_radian:
            speed = math.radians(speed)
        ret = self.arm_cmd.set_reduced_jointspeed(speed)
        logger.info('API -> set_reduced_linespeed -> ret={}, speed={}'.format(ret[0], speed))
        return ret[0]

    @xarm_is_connected(_type='get')
    def get_reduced_mode(self):
        ret = self.arm_cmd.get_reduced_mode()
        if ret[0] in [0, XCONF.UxbusState.ERR_CODE, XCONF.UxbusState.WAR_CODE]:
            ret[0] = 0
        return ret[0], ret[1]

    @xarm_is_connected(_type='get')
    def get_reduced_states(self, is_radian=None):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        ret = self.arm_cmd.get_reduced_states(79 if self.version_is_ge_1_2_11 else 21)
        if ret[0] in [0, XCONF.UxbusState.ERR_CODE, XCONF.UxbusState.WAR_CODE]:
            ret[0] = 0
            if not is_radian:
                ret[4] = round(math.degrees(ret[4]), 1)
                if self.version_is_ge_1_2_11:
                    # ret[5] = list(map(math.degrees, ret[5]))
                    ret[5] = list(map(lambda x: round(math.degrees(x), 2), ret[5]))
        return ret[0], ret[1:]

    @xarm_is_connected(_type='set')
    def set_reduced_tcp_boundary(self, boundary):
        assert len(boundary) >= 6
        boundary = list(map(int, boundary))
        limits = [0] * 6
        limits[0:2] = boundary[0:2] if boundary[0] >= boundary[1] else boundary[0:2][::-1]
        limits[2:4] = boundary[2:4] if boundary[2] >= boundary[3] else boundary[2:4][::-1]
        limits[4:6] = boundary[4:6] if boundary[4] >= boundary[5] else boundary[4:6][::-1]
        ret = self.arm_cmd.set_xyz_limits(limits)
        logger.info('API -> set_reduced_tcp_boundary -> ret={}, boundary={}'.format(ret[0], limits))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_reduced_joint_range(self, joint_range, is_radian=None):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        assert len(joint_range) >= self.axis * 2
        joint_range = list(map(float, joint_range))
        limits = [0] * 14
        for i in range(7):
            if i < self.axis:
                limits[i*2:i*2+2] = joint_range[i*2:i*2+2] if joint_range[i*2] <= joint_range[i*2+1] else joint_range[i*2:i*2+2][::-1]
        if not is_radian:
            limits = list(map(math.radians, limits))
            # limits = list(map(lambda x: round(math.radians(x), 3), limits))

        for i in range(self.axis):
            joint_limit = XCONF.Robot.JOINT_LIMITS.get(self.axis).get(self.device_type, [])
            if i < len(joint_limit):
                angle_range = joint_limit[i]
                # angle_range = list(map(lambda x: round(x, 3), joint_limit[i]))
                if limits[i * 2] < angle_range[0]:
                    limits[i * 2] = angle_range[0]
                if limits[i * 2 + 1] > angle_range[1]:
                    limits[i * 2 + 1] = angle_range[1]
                if limits[i * 2] >= angle_range[1]:
                    return APIState.OUT_OF_RANGE
                if limits[i * 2 + 1] <= angle_range[0]:
                    return APIState.OUT_OF_RANGE
        ret = self.arm_cmd.set_reduced_jrange(limits)
        logger.info('API -> set_reduced_joint_range -> ret={}, boundary={}'.format(ret[0], limits))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_fense_mode(self, on_off):
        ret = self.arm_cmd.set_fense_on(on_off)
        logger.info('API -> set_fense_mode -> ret={}, on={}'.format(ret[0], on_off))
        return ret

    @xarm_is_connected(_type='set')
    def set_collision_rebound(self, on_off):
        ret = self.arm_cmd.set_collis_reb(on_off)
        logger.info('API -> set_collision_rebound -> ret={}, on={}'.format(ret[0], on_off))
        return ret

    @xarm_is_connected(_type='set')
    def set_timer(self, secs_later, tid, fun_code, param1=0, param2=0):
        ret = self.arm_cmd.set_timer(secs_later, tid, fun_code, param1, param2)
        return ret[0]

    @xarm_is_connected(_type='set')
    def cancel_timer(self, tid):
        ret = self.arm_cmd.cancel_timer(tid)
        return ret[0]

    @xarm_is_connected(_type='set')
    @xarm_is_pause(_type='set')
    def set_world_offset(self, offset, is_radian=None):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        assert isinstance(offset, Iterable) and len(offset) >= 6
        world_offset = [0] * 6
        for i in range(len(offset)):
            if not offset[i]:
                continue
            if i < 3:
                world_offset[i] = offset[i]
            elif i < 6:
                if not is_radian:
                    world_offset[i] = math.radians(offset[i])
                else:
                    world_offset[i] = offset[i]
        ret = self.arm_cmd.set_world_offset(world_offset)
        logger.info('API -> set_world_offset -> ret={}, offset={}'.format(ret[0], world_offset))
        return ret[0]

    def reset(self, speed=None, mvacc=None, mvtime=None, is_radian=None, wait=False, timeout=None):
        logger.info('reset--begin')
        is_radian = self._default_is_radian if is_radian is None else is_radian
        if not self._enable_report or self._stream_type != 'socket':
            self.get_err_warn_code()
            self.get_state()
        if self._warn_code != 0:
            self.clean_warn()
        if self._error_code != 0:
            self.clean_error()
            self.motion_enable(enable=True, servo_id=8)
            self.set_state(0)
        if not self._is_ready:
            self.motion_enable(enable=True, servo_id=8)
            self.set_state(state=0)
        self.move_gohome(speed=speed, mvacc=mvacc, mvtime=mvtime, is_radian=is_radian, wait=wait, timeout=timeout)
        logger.info('reset--end')

    @xarm_is_ready(_type='set')
    def set_joints_torque(self, joints_torque):
        ret = self.arm_cmd.set_servot(joints_torque)
        logger.info('API -> set_joints_torque -> ret={}, joints_torque={}'.format(ret[0], joints_torque))
        return ret[0]

    @xarm_is_connected(_type='get')
    def get_joints_torque(self, servo_id=None):
        ret = self.arm_cmd.get_joint_tau()
        if ret[0] in [0, XCONF.UxbusState.ERR_CODE, XCONF.UxbusState.WAR_CODE] and len(ret) > 7:
            self._joints_torque = [float('{:.6f}'.format(ret[i])) for i in range(1, 8)]
            ret[0] = 0
        if servo_id is None or servo_id == 8 or len(self._joints_torque) < servo_id:
            return ret[0], list(self._joints_torque)
        else:
            return ret[0], self._joints_torque[servo_id - 1]

    @xarm_is_connected(_type='get')
    def get_safe_level(self):
        ret = self.arm_cmd.get_safe_level()
        return ret[0], ret[1]

    @xarm_is_connected(_type='set')
    def set_safe_level(self, level=4):
        ret = self.arm_cmd.set_safe_level(level)
        logger.info('API -> set_safe_level -> ret={}, level={}'.format(ret[0], level))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_pause_time(self, sltime, wait=False):
        assert isinstance(sltime, (int, float))
        ret = self.arm_cmd.sleep_instruction(sltime)
        if wait:
            time.sleep(sltime)
        else:
            if time.time() >= self._sleep_finish_time:
                self._sleep_finish_time = time.time() + sltime
            else:
                self._sleep_finish_time += sltime
        logger.info('API -> set_pause_time -> ret={}, sltime={}'.format(ret[0], sltime))
        return ret[0]

    def set_sleep_time(self, sltime, wait=False):
        return self.set_pause_time(sltime, wait)

    @xarm_is_connected(_type='set')
    @xarm_is_pause(_type='set')
    def set_tcp_offset(self, offset, is_radian=None):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        assert isinstance(offset, Iterable) and len(offset) >= 6
        tcp_offset = [0] * 6
        for i in range(len(offset)):
            if not offset[i]:
                continue
            if i < 3:
                tcp_offset[i] = offset[i]
            elif i < 6:
                if not is_radian:
                    tcp_offset[i] = math.radians(offset[i])
                else:
                    tcp_offset[i] = offset[i]
        ret = self.arm_cmd.set_tcp_offset(tcp_offset)
        logger.info('API -> set_tcp_offset -> ret={}, offset={}'.format(ret[0], tcp_offset))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_tcp_jerk(self, jerk):
        ret = self.arm_cmd.set_tcp_jerk(jerk)
        logger.info('API -> set_tcp_jerk -> ret={}, jerk={}'.format(ret[0], jerk))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_tcp_maxacc(self, acc):
        ret = self.arm_cmd.set_tcp_maxacc(acc)
        logger.info('API -> set_tcp_maxacc -> ret={}, maxacc={}'.format(ret[0], acc))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_joint_jerk(self, jerk, is_radian=None):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        _jerk = jerk
        if not is_radian:
            _jerk = math.radians(_jerk)
        ret = self.arm_cmd.set_joint_jerk(_jerk)
        logger.info('API -> set_joint_jerk -> ret={}, jerk={}'.format(ret[0], _jerk))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_joint_maxacc(self, acc, is_radian=None):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        _acc = acc
        if not is_radian:
            _acc = math.radians(acc)
        ret = self.arm_cmd.set_joint_maxacc(_acc)
        logger.info('API -> set_joint_maxacc -> ret={}, maxacc={}'.format(ret[0], acc))
        return ret[0]

    @xarm_is_connected(_type='set')
    @xarm_is_pause(_type='set')
    def set_tcp_load(self, weight, center_of_gravity):
        if compare_version(self.version_number, (0, 2, 0)):
            _center_of_gravity = center_of_gravity
        else:
            _center_of_gravity = [item / 1000.0 for item in center_of_gravity]
        ret = self.arm_cmd.set_tcp_load(weight, _center_of_gravity)
        logger.info('API -> set_tcp_load -> ret={}, weight={}, center={}'.format(ret[0], weight, _center_of_gravity))
        return ret[0]

    @xarm_is_connected(_type='set')
    @xarm_is_pause(_type='set')
    def set_collision_sensitivity(self, value):
        assert isinstance(value, int) and 0 <= value <= 5
        ret = self.arm_cmd.set_collis_sens(value)
        logger.info('API -> set_collision_sensitivity -> ret={}, sensitivity={}'.format(ret[0], value))
        return ret[0]

    @xarm_is_connected(_type='set')
    @xarm_is_pause(_type='set')
    def set_teach_sensitivity(self, value):
        assert isinstance(value, int) and 1 <= value <= 5
        ret = self.arm_cmd.set_teach_sens(value)
        logger.info('API -> set_teach_sensitivity -> ret={}, sensitivity={}'.format(ret[0], value))
        return ret[0]

    @xarm_is_connected(_type='set')
    @xarm_is_pause(_type='set')
    def set_gravity_direction(self, direction):
        ret = self.arm_cmd.set_gravity_dir(direction[:3])
        logger.info('API -> set_gravity_direction -> ret={}, direction={}'.format(ret[0], direction))
        return ret[0]

    @xarm_is_connected(_type='set')
    @xarm_is_pause(_type='set')
    def set_mount_direction(self, base_tilt_deg, rotation_deg, is_radian=None):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        t1 = base_tilt_deg
        t2 = rotation_deg

        if not is_radian:
            t1 = math.radians(t1)
            t2 = math.radians(t2)

        # original G vect mounted on flat surface
        G_normal = [0, 0, -1]

        # rotation matrix introduced by 2 mounting angles
        R2 = [math.cos(-t2), -math.sin(-t2), 0, math.sin(-t2), math.cos(-t2), 0, 0, 0, 1]
        R1 = [math.cos(-t1), 0, math.sin(-t1), 0, 1, 0, -math.sin(-t1), 0, math.cos(-t1)]

        Rot = [0] * 9
        g_new = [0] * 3

        # Mat(Rot) = Mat(R2)*Mat(R1)
        # vect(g_new) = Mat(Rot)*vect(G_normal)
        for i in range(3):
            for j in range(3):
                Rot[i * 3 + j] += (
                R2[i * 3 + 0] * R1[0 * 3 + j] + R2[i * 3 + 1] * R1[1 * 3 + j] + R2[i * 3 + 2] * R1[2 * 3 + j])

            g_new[i] = Rot[i * 3 + 0] * G_normal[0] + Rot[i * 3 + 1] * G_normal[1] + Rot[i * 3 + 2] * G_normal[2]

        ret = self.arm_cmd.set_gravity_dir(g_new)
        logger.info('API -> set_mount_direction -> ret={}, tilt={}, rotation={}, direction={}'.format(ret[0], base_tilt_deg, rotation_deg, g_new))
        return ret[0]

    @xarm_is_connected(_type='set')
    def clean_conf(self):
        ret = self.arm_cmd.clean_conf()
        logger.info('API -> clean_conf -> ret={}'.format(ret[0]))
        return ret[0]

    @xarm_is_connected(_type='set')
    def save_conf(self):
        ret = self.arm_cmd.save_conf()
        logger.info('API -> save_conf -> ret={}'.format(ret[0]))
        return ret[0]

    @xarm_is_connected(_type='get')
    def get_inverse_kinematics(self, pose, input_is_radian=None, return_is_radian=None):
        input_is_radian = self._default_is_radian if input_is_radian is None else input_is_radian
        return_is_radian = self._default_is_radian if return_is_radian is None else return_is_radian
        assert len(pose) >= 6
        if not input_is_radian:
            pose = [pose[i] if i < 3 else math.radians(pose[i]) for i in range(6)]
        ret = self.arm_cmd.get_ik(pose)
        angles = []
        if ret[0] in [0, XCONF.UxbusState.ERR_CODE, XCONF.UxbusState.WAR_CODE]:
            # angles = [ret[i][0] for i in range(1, 8)]
            angles = [ret[i] for i in range(1, 8)]
            ret[0] = 0
            if not return_is_radian:
                angles = [math.degrees(angle) for angle in angles]
        return ret[0], angles

    @xarm_is_connected(_type='get')
    def get_forward_kinematics(self, angles, input_is_radian=None, return_is_radian=None):
        input_is_radian = self._default_is_radian if input_is_radian is None else input_is_radian
        return_is_radian = self._default_is_radian if return_is_radian is None else return_is_radian
        # assert len(angles) >= 7
        if not input_is_radian:
            angles = [math.radians(angles[i]) for i in range(len(angles))]

        new_angles = [0] * 7
        for i in range(min(len(angles), 7)):
            new_angles[i] = angles[i]

        ret = self.arm_cmd.get_fk(new_angles)
        pose = []
        if ret[0] in [0, XCONF.UxbusState.ERR_CODE, XCONF.UxbusState.WAR_CODE]:
            # pose = [ret[i][0] for i in range(1, 7)]
            pose = [ret[i] for i in range(1, 7)]
            ret[0] = 0
            if not return_is_radian:
                pose = [pose[i] if i < 3 else math.degrees(pose[i]) for i in range(len(pose))]
        return ret[0], pose

    @xarm_is_connected(_type='get')
    def is_tcp_limit(self, pose, is_radian=None):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        assert len(pose) >= 6
        for i in range(6):
            if isinstance(pose[i], str):
                pose[i] = float(pose[i])
            if pose[i] is None:
                pose[i] = self._last_position[i]
            elif i > 2 and not is_radian:
                pose[i] = math.radians(pose[i])
        ret = self.arm_cmd.is_tcp_limit(pose)
        logger.info('API -> is_tcp_limit -> ret={}, limit={}'.format(ret[0], ret[1]))
        if ret[0] in [0, XCONF.UxbusState.ERR_CODE, XCONF.UxbusState.WAR_CODE]:
            ret[0] = 0
            return ret[0], bool(ret[1])
        else:
            return ret[0], None

    @xarm_is_connected(_type='get')
    def is_joint_limit(self, joint, is_radian=None):
        is_radian = self._default_is_radian if is_radian is None else is_radian
        # assert len(joint) >= 7
        for i in range(len(joint)):
            if isinstance(joint[i], str):
                joint[i] = float(joint[i])
            if joint[i] is None:
                joint[i] = self._last_angles[i]
            elif not is_radian:
                joint[i] = math.radians(joint[i])

        new_angles = [0] * 7
        for i in range(min(len(joint), 7)):
            new_angles[i] = joint[i]

        ret = self.arm_cmd.is_joint_limit(new_angles)
        logger.info('API -> is_joint_limit -> ret={}, limit={}'.format(ret[0], ret[1]))
        if ret[0] in [0, XCONF.UxbusState.ERR_CODE, XCONF.UxbusState.WAR_CODE]:
            ret[0] = 0
            return ret[0], bool(ret[1])
        else:
            return ret[0], None

    def emergency_stop(self):
        logger.info('emergency_stop--begin')
        self.set_state(4)
        expired = time.time() + 3
        while self.state not in [4] and time.time() < expired:
            self.set_state(4)
            time.sleep(0.1)
        self._is_stop = True
        self._sleep_finish_time = 0
        logger.info('emergency_stop--end')

    def send_cmd_async(self, command, timeout=None):
        pass

    def send_cmd_sync(self, command=None):
        if command is None:
            return 0
        command = command.upper()
        return self._handle_gcode(command)

    def _handle_gcode(self, command):
        def __handle_gcode_g(num):
            if num == 1:  # G1 move_line, ex: G1 X{} Y{} Z{} A{roll} B{pitch} C{yaw} F{speed} Q{acc} T{}
                mvvelo = gcode_p.get_mvvelo(command)
                mvacc = gcode_p.get_mvacc(command)
                mvtime = gcode_p.get_mvtime(command)
                mvpose = gcode_p.get_poses(command)
                ret = self.set_position(*mvpose, radius=-1, speed=mvvelo, mvacc=mvacc, mvtime=mvtime)
            elif num == 2:  # G2 move_circle, ex: G2 X{} Y{} Z{} A{} B{} C{} I{} J{} K{} L{} M{} N{} F{speed} Q{acc} T{}
                mvvelo = gcode_p.get_mvvelo(command)
                mvacc = gcode_p.get_mvacc(command)
                mvtime = gcode_p.get_mvtime(command)
                pos1 = gcode_p.get_poses(command, default=0)
                pos2 = gcode_p.get_joints(command, default=0)[:6]
                percent = gcode_p.get_mvradius(command, default=0)
                ret = self.move_circle(pos1, pos2, percent=percent, speed=mvvelo, mvacc=mvacc, mvtime=mvtime)
            elif num == 4:  # G4 set_pause_time, ex: G4 T{}
                sltime = gcode_p.get_mvtime(command, default=0)
                ret = self.set_pause_time(sltime)
            elif num == 7:  # G7 move_joint, ex: G7 I{} J{} K{} L{} M{} N{} O{} F{} Q{} T{}
                mvvelo = gcode_p.get_mvvelo(command)
                mvacc = gcode_p.get_mvacc(command)
                mvtime = gcode_p.get_mvtime(command)
                mvjoint = gcode_p.get_joints(command)
                ret = self.set_servo_angle(angle=mvjoint, speed=mvvelo, mvacc=mvacc, mvtime=mvtime)
            elif num == 8:  # G8 move_gohome, ex: G8 F{} Q{} T{}
                mvvelo = gcode_p.get_mvvelo(command)
                mvacc = gcode_p.get_mvacc(command)
                mvtime = gcode_p.get_mvtime(command)
                ret = self.move_gohome(speed=mvvelo, mvacc=mvacc, mvtime=mvtime)
            elif num == 9:  # G9 move_arc_line, ex: G9 X{} Y{} Z{} A{roll} B{pitch} C{yaw} R{radius} F{speed} Q{acc} T{}
                mvvelo = gcode_p.get_mvvelo(command)
                mvacc = gcode_p.get_mvacc(command)
                mvtime = gcode_p.get_mvtime(command)
                mvpose = gcode_p.get_poses(command)
                mvradii = gcode_p.get_mvradius(command, default=0)
                ret = self.set_position(*mvpose, speed=mvvelo, mvacc=mvacc, mvtime=mvtime, radius=mvradii)
            elif num == 11:  # G11 set_servo_angle_j, ex: G11 I{} J{} K{} L{} M{} N{} O{} F{} Q{} T{}
                mvvelo = gcode_p.get_mvvelo(command)
                mvacc = gcode_p.get_mvacc(command)
                mvtime = gcode_p.get_mvtime(command)
                mvjoint = gcode_p.get_joints(command, default=0)
                ret = self.set_servo_angle_j(mvjoint, speed=mvvelo, mvacc=mvacc, mvtime=mvtime)
            elif num == 12:  # G12 sleep, ex: G12 T{}
                mvtime = gcode_p.get_mvtime(command, default=0)
                time.sleep(mvtime)
                ret = 0
            else:
                logger.debug('command {} is not exist'.format(command))
                ret = APIState.CMD_NOT_EXIST, 'command {} is not exist'.format(command)
            return ret

        def __handle_gcode_h(num):
            if num == 1:  # H1 get_version, ex: H1
                ret = self.get_version()
            elif num == 10:  # H10 shutdown_system, ex: H10 V{}
                value = gcode_p.get_int_value(command, default=0)
                ret = self.shutdown_system(value)
            elif num == 11:  # H11 motion_enable, ex: H11 I{id} V{enable}
                value = gcode_p.get_int_value(command)
                servo_id = gcode_p.get_id_num(command, default=0)
                ret = self.motion_enable(enable=value, servo_id=servo_id)
            elif num == 12:  # H12 set_state, ex: H12 V{state}
                value = gcode_p.get_int_value(command, default=0)
                ret = self.set_state(value)
            elif num == 13:  # H13 get_state, ex: H13
                ret = self.get_state()
            elif num == 14:  # H14 get_cmd_num, ex: H14
                ret = self.get_cmdnum()
            elif num == 15:  # H15 get_error_warn_code, ex: H15
                ret = self.get_err_warn_code()
            elif num == 16:  # H16 clean_error, ex: H16
                ret = self.clean_error()
            elif num == 17:  # H17 clean_warn, ex: H17
                ret = self.clean_warn()
            elif num == 18:  # H18 set_brake, ex: H18 I{id} V{open}
                value = gcode_p.get_int_value(command)
                servo_id = gcode_p.get_id_num(command, default=0)
                ret = self.arm_cmd.set_brake(servo_id, value)[0]
            elif num == 19:  # H19 set_mode, ex: H19 V{mode}
                value = gcode_p.get_int_value(command, default=0)
                ret = self.set_mode(value)
            elif num == 31:  # H31 set_tcp_jerk, ex: H31 V{jerk}
                value = gcode_p.get_float_value(command, default=-1)
                ret = self.set_tcp_jerk(value)
            elif num == 32:  # H32 set_tcp_maxacc, ex: H32 V{maxacc}
                value = gcode_p.get_float_value(command, default=-1)
                ret = self.set_tcp_maxacc(value)
            elif num == 33:  # H33 set_joint_jerk, ex: H33 V{jerk}
                value = gcode_p.get_float_value(command, default=-1)
                ret = self.set_joint_jerk(value)
            elif num == 34:  # H34 set_joint_maxacc, ex: H34 V{maxacc}
                value = gcode_p.get_float_value(command, default=-1)
                ret = self.set_joint_maxacc(value)
            elif num == 35:  # H35 set_tcp_offset, ex: H35 X{x} Y{y} Z{z} A{roll} B{pitch} C{yaw}
                pose = gcode_p.get_poses(command)
                ret = self.set_tcp_offset(pose)
            elif num == 36:  # H36 set_tcp_load, ex: H36 I{weight} J{center_x} K{center_y} L{center_z}
                values = gcode_p.get_joints(command, default=0)
                ret = self.set_tcp_load(values[0], values[1:4])
            elif num == 37:  # H37 set_collision_sensitivity, ex: H37 V{sensitivity}
                value = gcode_p.get_int_value(command, default=0)
                ret = self.set_collision_sensitivity(value)
            elif num == 38:  # H38 set_teach_sensitivity, ex: H38 V{sensitivity}
                value = gcode_p.get_int_value(command, default=0)
                ret = self.set_teach_sensitivity(value)
            elif num == 39:  # H39 clean_conf, ex: H39
                ret = self.clean_conf()
            elif num == 40:  # H40 save_conf, ex: H40
                ret = self.save_conf()
            elif num == 41:  # H41 get_position, ex: H41
                ret = self.get_position()
            elif num == 42:  # H42 get_servo_angle, ex: H42
                ret = self.get_servo_angle()
            elif num == 43:  # H43 get_ik, ex: H43 X{} Y{} Z{} A{roll} B{pitch} C{yaw}
                pose = gcode_p.get_poses(command, default=0)
                ret = self.get_inverse_kinematics(pose, input_is_radian=False, return_is_radian=False)
            elif num == 44:  # H44 get_fk, ex: H44 I{} J{} K{} L{} M{} N{} O{}
                joint = gcode_p.get_joints(command, default=0)
                ret = self.get_forward_kinematics(joint, input_is_radian=False, return_is_radian=False)
            elif num == 45:  # H45 is_joint_limit, ex: H45 I{} J{} K{} L{} M{} N{} O{}
                joint = gcode_p.get_joints(command)
                ret = self.is_joint_limit(joint, is_radian=False)
            elif num == 46:  # H46 is_tcp_limit, ex: H46 X{} Y{} Z{} A{roll} B{pitch} C{yaw}
                pose = gcode_p.get_poses(command)
                ret = self.is_tcp_limit(pose, is_radian=False)
            elif num == 51:  # H51 set_gravity_direction, ex: H51 X{} Y{} Z{} A{roll} B{pitch} C{yaw}
                pose = gcode_p.get_poses(command, default=0)
                ret = self.set_gravity_direction(pose)
            elif num == 101:  # H101 set_servo_addr_16, ex: H101 I{id} D{addr} V{value}
                value = gcode_p.get_int_value(command)
                servo_id = gcode_p.get_id_num(command, default=0)
                addr = gcode_p.get_addr(command)
                ret = self.set_servo_addr_16(servo_id=servo_id, addr=addr, value=value)
            elif num == 102:  # H102 get_servo_addr_16, ex: H102 I{id} D{addr}
                servo_id = gcode_p.get_id_num(command, default=0)
                addr = gcode_p.get_addr(command)
                ret = self.get_servo_addr_16(servo_id=servo_id, addr=addr)
            elif num == 103:  # H103 set_servo_addr_32, ex: H103 I{id} D{addr} V{value}
                servo_id = gcode_p.get_id_num(command, default=0)
                addr = gcode_p.get_addr(command)
                value = gcode_p.get_int_value(command)
                ret = self.set_servo_addr_32(servo_id=servo_id, addr=addr, value=value)
            elif num == 104:  # H104 get_servo_addr_32, ex: H104 I{id} D{addr}
                servo_id = gcode_p.get_id_num(command, default=0)
                addr = gcode_p.get_addr(command)
                ret = self.get_servo_addr_32(servo_id=servo_id, addr=addr)
            elif num == 105:  # H105 set_servo_zero, ex: H105 I{id}
                servo_id = gcode_p.get_id_num(command, default=0)
                ret = self.set_servo_zero(servo_id=servo_id)
            elif num == 106:  # H106 get_servo_debug_msg, ex: H106
                ret = self.get_servo_debug_msg()
            else:
                logger.debug('command {} is not exist'.format(command))
                ret = APIState.CMD_NOT_EXIST, 'command {} is not exist'.format(command)
            return ret

        def __handle_gcode_m(num):
            if num == 116:  # M116 set_gripper_enable, ex: M116 V{enable}
                value = gcode_p.get_int_value(command)
                ret = self.set_gripper_enable(value)
            elif num == 117:  # M117 set_gripper_mode, ex: M117 V{mode}
                value = gcode_p.get_int_value(command)
                ret = self.set_gripper_mode(value)
            elif num == 118:  # M118 set_gripper_zero, ex: M118
                ret = self.set_gripper_zero()
            elif num == 119:  # M119 get_gripper_position, ex: M119
                ret = self.get_gripper_position()
            elif num == 120:  # M120 set_gripper_position, ex: M120 V{pos}
                value = gcode_p.get_int_value(command)
                ret = self.set_gripper_position(value)
            elif num == 121:  # M121 set_gripper_speed, ex: M121 V{speed}
                value = gcode_p.get_int_value(command)
                ret = self.set_gripper_speed(value)
            elif num == 125:  # M125 get_gripper_err_code, ex: M125
                ret = self.get_gripper_err_code()
            elif num == 126:  # M126 clean_gripper_error, ex: M126
                ret = self.clean_gripper_error()
            elif num == 127:
                ret = self.get_gripper_version()
            elif num == 131:  # M131 get_tgpio_digital, ex: M131
                ret = self.get_tgpio_digital()
            elif num == 132:  # M132 set_tgpio_digital, ex: M132 I{ionum} V{}
                ionum = gcode_p.get_id_num(command, default=0)
                value = gcode_p.get_int_value(command)
                ret = self.set_tgpio_digital(ionum, value)
            elif num == 133:  # M133 get_tgpio_analog(0), ex: M133 I{ionum=0}
                ionum = gcode_p.get_id_num(command, default=0)
                ret = self.get_tgpio_analog(ionum=ionum)
            elif num == 134:  # M134 get_tgpio_analog(1), ex: M134 I{ionum=1}
                ionum = gcode_p.get_id_num(command, default=0)
                ret = self.get_tgpio_analog(ionum=ionum)
            elif num == 135:
                return self.get_tgpio_version()
            else:
                logger.debug('command {} is not exist'.format(command))
                ret = APIState.CMD_NOT_EXIST, 'command {} is not exist'.format(command)
            return ret

        def __handle_gcode_d(num):
            if num == 11:  # D11 I{id}
                id_num = gcode_p.get_id_num(command, default=None)
                ret = self.get_servo_error_code(id_num)
            elif num == 12:  # D12 I{id}
                id_num = gcode_p.get_id_num(command, default=None)
                if id_num == 0:
                    id_num = 8
                self.clean_error()
                self.clean_warn()
                self.motion_enable(enable=False, servo_id=id_num)
                ret = self.set_servo_detach(id_num)
            elif num == 13:  # D13 I{id}
                id_num = gcode_p.get_id_num(command, default=None)
                if id_num == 0:
                    id_num = 8
                self.set_servo_zero(id_num)
                ret = self.motion_enable(enable=True, servo_id=id_num)
            elif num == 21:  # D21 I{id}
                id_num = gcode_p.get_id_num(command, default=None)
                self.clean_servo_pvl_err(id_num)
                ret = self.get_servo_error_code(id_num)
            else:
                logger.debug('command {} is not exist'.format(command))
                ret = APIState.CMD_NOT_EXIST, 'command {} is not exist'.format(command)
            return ret

        def __handle_gcode_s(num):
            if num == 44:  # S44 I{id}
                id_num = gcode_p.get_id_num(command, default=None)
                ret = self.get_servo_all_pids(id_num)
            elif num == 45:
                id_num = gcode_p.get_id_num(command, default=1)
                ret = self.get_servo_version(servo_id=id_num)
            else:
                logger.debug('command {} is not exist'.format(command))
                ret = APIState.CMD_NOT_EXIST, 'command {} is not exist'.format(command)
            return ret

        def __handle_gcode_c(num):
            if num == 131:  # C131 get_cgpio_digital, ex: C131
                ret = self.get_cgpio_digital()
            elif num == 132:  # C132 get_cgpio_analog(0), ex: C132 I{ionum=0}
                ionum = gcode_p.get_id_num(command, default=0)
                ret = self.get_cgpio_analog(ionum)
            elif num == 133:  # C133 get_cgpio_analog(1), ex: C133 I{ionum=1}
                ionum = gcode_p.get_id_num(command, default=1)
                ret = self.get_cgpio_analog(ionum)
            elif num == 134:  # C134 set_cgpio_digital, ex: C134 I{ionum} V{value}
                ionum = gcode_p.get_id_num(command, default=0)
                value = gcode_p.get_int_value(command)
                ret = self.set_cgpio_digital(ionum, value)
            elif num == 135:  # C135 set_cgpio_analog(0, v), ex: C135 I{ionum=0} V{value}
                ionum = gcode_p.get_id_num(command, default=0)
                value = gcode_p.get_float_value(command)
                ret = self.set_cgpio_analog(ionum, value)
            elif num == 136:  # C136 set_cgpio_analog(1, v), ex: C136 I{ionum=1} V{value}
                ionum = gcode_p.get_id_num(command, default=1)
                value = gcode_p.get_float_value(command)
                ret = self.set_cgpio_analog(ionum, value)
            elif num == 137:  # C137 set_cgpio_digital_input_function, ex: C137 I{ionum} V{fun}
                ionum = gcode_p.get_id_num(command, default=0)
                value = gcode_p.get_int_value(command)
                ret = self.set_cgpio_digital_input_function(ionum, value)
            elif num == 138:  # C138 set_cgpio_digital_output_function, ex: C138 I{ionum} V{fun}
                ionum = gcode_p.get_id_num(command, default=0)
                value = gcode_p.get_int_value(command)
                ret = self.set_cgpio_digital_output_function(ionum, value)
            elif num == 139:  # C139 get_cgpio_state, ex: C139
                ret = self.get_cgpio_state()
            else:
                logger.debug('command {} is not exist'.format(command))
                ret = APIState.CMD_NOT_EXIST, 'command {} is not exist'.format(command)
            return ret

        cmd_num = gcode_p.get_gcode_cmd_num(command, 'G')
        if cmd_num >= 0:
            return __handle_gcode_g(cmd_num)
        cmd_num = gcode_p.get_gcode_cmd_num(command, 'H')
        if cmd_num >= 0:
            return __handle_gcode_h(cmd_num)
        cmd_num = gcode_p.get_gcode_cmd_num(command, 'M')
        if cmd_num >= 0:
            return __handle_gcode_m(cmd_num)
        cmd_num = gcode_p.get_gcode_cmd_num(command, 'D')
        if cmd_num >= 0:
            return __handle_gcode_d(cmd_num)
        cmd_num = gcode_p.get_gcode_cmd_num(command, 'S')
        if cmd_num >= 0:
            return __handle_gcode_s(cmd_num)
        cmd_num = gcode_p.get_gcode_cmd_num(command, 'C')
        if cmd_num >= 0:
            return __handle_gcode_c(cmd_num)
        logger.debug('command {} is not exist'.format(command))
        return APIState.CMD_NOT_EXIST, 'command {} is not exist'.format(command)

    @xarm_is_connected(_type='set')
    def run_gcode_file(self, path, **kwargs):
        times = kwargs.get('times', 1)
        init = kwargs.get('init', False)
        mode = kwargs.get('mode', 0)
        state = kwargs.get('state', 0)
        wait_seconds = kwargs.get('wait_seconds', 0)
        try:
            abs_path = os.path.abspath(path)
            if not os.path.exists(abs_path):
                raise FileNotFoundError
            with open(abs_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            lines = [line.strip() for line in lines]
            if init:
                self.clean_error()
                self.clean_warn()
                self.motion_enable(True)
                self.set_mode(mode)
                self.set_state(state)
            if wait_seconds > 0:
                time.sleep(wait_seconds)

            for i in range(times):
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if not self.connected:
                        logger.error('xArm is disconnect')
                        return APIState.NOT_CONNECTED
                    ret = self.send_cmd_sync(line)
                    if isinstance(ret, int) and ret < 0:
                        return ret
            return APIState.NORMAL
        except Exception as e:
            logger.error(e)
            return APIState.API_EXCEPTION

    @xarm_is_connected(_type='set')
    def run_blockly_app(self, path, **kwargs):
        """
        Run the app generated by xArmStudio software
        :param path: app path
        """
        try:
            if not os.path.exists(path):
                path = os.path.join(os.path.expanduser('~'), '.UFACTORY', 'projects', 'test', 'xarm{}'.format(self.axis), 'app', 'myapp', path)
            if os.path.isdir(path):
                path = os.path.join(path, 'app.xml')
            if not os.path.exists(path):
                raise FileNotFoundError
            blockly_tool = BlocklyTool(path)
            succeed = blockly_tool.to_python(arm=self, **kwargs)
            if succeed:
                times = kwargs.get('times', 1)
                for i in range(times):
                    exec(blockly_tool.codes, {'arm': self})
                return APIState.NORMAL
            else:
                logger.error('The conversion is incomplete and some blocks are not yet supported.')
                return APIState.CONVERT_FAILED
        except Exception as e:
            logger.error(e)
            return APIState.API_EXCEPTION

    @xarm_is_connected(_type='get')
    def get_hd_types(self):
        ret = self.arm_cmd.get_hd_types()
        return ret[0], ret[1:]

    @xarm_is_connected(_type='set')
    def reload_dynamics(self):
        ret = self.arm_cmd.reload_dynamics()
        if ret[0] in [0, XCONF.UxbusState.ERR_CODE, XCONF.UxbusState.WAR_CODE]:
            ret[0] = 0
        logger.info('API -> reload_dynamics -> ret={}'.format(ret[0]))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_counter_reset(self):
        ret = self.arm_cmd.cnter_reset()
        # if ret[0] in [0, XCONF.UxbusState.ERR_CODE, XCONF.UxbusState.WAR_CODE]:
        #     ret[0] = 0
        logger.info('API -> set_counter_reset -> ret={}'.format(ret[0]))
        return ret[0]

    @xarm_is_connected(_type='set')
    def set_counter_increase(self, val=1):
        ret = self.arm_cmd.cnter_plus()
        # if ret[0] in [0, XCONF.UxbusState.ERR_CODE, XCONF.UxbusState.WAR_CODE]:
        #     ret[0] = 0
        logger.info('API -> set_counter_increase -> ret={}'.format(ret[0]))
        return ret[0]

    @staticmethod
    def set_timeout(timeout):
        if isinstance(timeout, (tuple, list)) and len(timeout) >= 2:
            XCONF.UxbusConf.SET_TIMEOUT = timeout[0] * 1000
            XCONF.UxbusConf.GET_TIMEOUT = timeout[1] * 1000
        else:
            XCONF.UxbusConf.SET_TIMEOUT = timeout * 1000
            XCONF.UxbusConf.GET_TIMEOUT = timeout * 1000
        return 0

    @xarm_is_connected(_type='set')
    def set_report_tau_or_i(self, tau_or_i=0):
        ret = self.arm_cmd.set_report_tau_or_i(tau_or_i)
        if ret[0] in [0, XCONF.UxbusState.ERR_CODE, XCONF.UxbusState.WAR_CODE]:
            ret[0] = 0
        logger.info('API -> set_report_tau_or_i -> ret={}'.format(ret[0]))
        return ret[0]

    @xarm_is_connected(_type='get')
    def get_report_tau_or_i(self):
        ret = self.arm_cmd.get_report_tau_or_i()
        return ret[0], ret[1]

    def get_firmware_config(self):
        cgpio_code, cgpio_states = self.get_cgpio_state()
        reduced_code, reduced_states = self.get_reduced_states()
        tau_code, tau_flag = self.get_report_tau_or_i()
        code = cgpio_code if reduced_code == 0 and tau_code == 0 else reduced_code if cgpio_code == 0 and tau_code == 0 else tau_code
        return code, {
            'collision_sensitivity': self.collision_sensitivity,  # 碰撞灵敏度
            'teach_sensitivity': self.teach_sensitivity,  # 示教灵敏度
            'gravity_direction': self.gravity_direction,  # 重力方向
            'tcp_load': self.tcp_load,  # TCP负载
            'tcp_offset': self.position_offset,  # TCP偏移
            'tcp_maxacc': self.tcp_acc_limit[1],  # TCP的最大加速度
            'tcp_jerk': self.tcp_jerk,  # TCP加加速度
            'joint_maxacc': self.joint_acc_limit[1],  # 关节的最大加速度
            'joint_jerk': self.joint_jerk,  # 关节加加速度
            'world_offset': self.world_offset,  # 基坐标偏移
            'report_tau_or_i': tau_flag,  # 上报力矩还是电流
            'cgpio_auxin': cgpio_states[10],  # 控制器数字输入IO的配置功能
            'cgpio_auxout': cgpio_states[11],  # 控制器数字输出IO的配置功能
            'reduced_states': reduced_states,  # 缩减模式的状态
            'gpio_reset_config': self.gpio_reset_config,  # gpio自动复位配置
        }

    def set_firmware_config(self, config):
        code, old_config = self.get_firmware_config()
        if 'collision_sensitivity' in config and config['collision_sensitivity'] != old_config['collision_sensitivity']:
            self.set_collision_sensitivity(config['collision_sensitivity'])
        if 'teach_sensitivity' in config and config['teach_sensitivity'] != old_config['teach_sensitivity']:
            self.set_teach_sensitivity(config['teach_sensitivity'])
        if 'gravity_direction' in config and config['gravity_direction'] != old_config['gravity_direction']:
            self.set_gravity_direction(config['gravity_direction'])
        if 'tcp_load' in config and config['tcp_load'] != old_config['tcp_load']:
            self.set_tcp_load(config['tcp_load'])
        if 'tcp_offset' in config and config['tcp_offset'] != old_config['tcp_offset']:
            self.set_tcp_offset(config['tcp_offset'])
        if 'tcp_maxacc' in config and config['tcp_maxacc'] != old_config['tcp_maxacc']:
            self.set_tcp_maxacc(config['tcp_maxacc'])
        if 'tcp_jerk' in config and config['tcp_jerk'] != old_config['tcp_jerk']:
            self.set_tcp_jerk(config['tcp_jerk'])
        if 'joint_maxacc' in config and config['joint_maxacc'] != old_config['joint_maxacc']:
            self.set_joint_maxacc(config['joint_maxacc'])
        if 'joint_jerk' in config and config['joint_jerk'] != old_config['joint_jerk']:
            self.set_joint_jerk(config['joint_jerk'])
        if 'world_offset' in config and config['world_offset'] != old_config['world_offset']:
            self.set_world_offset(config['world_offset'])
        if 'report_tau_or_i' in config and config['report_tau_or_i'] != old_config['report_tau_or_i']:
            self.set_report_tau_or_i(config['report_tau_or_i'])
        if 'gpio_reset_config' in config and config['gpio_reset_config'] != old_config['gpio_reset_config']:
            self.config_io_reset_when_stop(0, config['gpio_reset_config'][0])
            self.config_io_reset_when_stop(1, config['gpio_reset_config'][1])

        if 'reduced_states' in config:
            states = config['reduced_states']
            old_states = old_config['reduced_states']
            if states[1] != old_states[1]:
                self.set_reduced_tcp_boundary(states[1])
            if states[2] != old_states[2]:
                self.set_reduced_max_tcp_speed(states[2])
            if states[3] != old_states[3]:
                self.set_reduced_max_joint_speed(states[3])
            if len(states) > 4 and len(old_states) > 4:
                if states[4] != old_states[4]:
                    self.set_reduced_joint_range(states[4])
            if len(states) > 5 and len(old_states) > 5:
                if states[5] != old_states[5]:
                    self.set_fense_mode(states[5])
            if len(states) > 6 and len(old_states) > 6:
                if states[4] != old_states[6]:
                    self.set_collision_rebound(states[6])
            self.set_reduced_mode(states[0])

        if 'cgpio_auxin' in config and config['cgpio_auxin'] != old_config['cgpio_auxin']:
            for i in range(len(config['cgpio_auxin'])):
                if config['cgpio_auxin'][i] != old_config['cgpio_auxin'][i]:
                    self.set_cgpio_digital_input_function(i, config['cgpio_auxin'][i])
        if 'cgpio_auxout' in config and config['cgpio_auxout'] != old_config['cgpio_auxout']:
            for i in range(len(config['cgpio_auxout'])):
                if config['cgpio_auxout'][i] != old_config['cgpio_auxout'][i]:
                    self.set_cgpio_digital_output_function(i, config['cgpio_auxout'][i])
