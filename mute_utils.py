from src.plugin_system.base.component_types import MaiMessages
from src.common.logger import get_logger
from src.config.config import global_config

logger = get_logger("Silence")

class MuteUtils:

    _personal_mute_records = {}  # 格式: {stream_id: True/False}
    _whole_mute_records = {}  # 格式: {stream_id: True/False}

    @classmethod
    def is_muted(cls, stream_id: str) -> bool:
        """
        检查聊天流是否被禁言
        返回：True/False
        """
        whole = cls._whole_mute_records.get(stream_id, False)
        personal = cls._personal_mute_records.get(stream_id, False)
        return personal or whole
    
    @classmethod
    def mute_check(cls, message: MaiMessages):
        """
        一个简易的禁言状态检查器
        """
        # 获取消息聊天流ID
        stream_id = message.stream_id

        if len(message.message_segments) == 1:
            seg = message.message_segments[0]
            self_id = int(global_config.bot.qq_account)
            
            if seg.type == "notify":
                data = seg.data

                if data.get("sub_type") == "ban":

                    banned_user_info = data.get("banned_user_info", {})

                    if banned_user_info.get("user_id") == self_id:

                        cls._personal_mute_records[stream_id] = True
           
                elif data.get("sub_type") == "whole_ban":

                    cls._whole_mute_records[stream_id] = True
                
                elif data.get("sub_type") == "lift_ban":

                    lifted_user_info = data.get("lifted_user_info", {})

                    if lifted_user_info.get("user_id") == self_id:

                        cls._personal_mute_records.pop(stream_id, None)

                elif data.get("sub_type") == "whole_lift_ban":

                    cls._whole_mute_records.pop(stream_id, None)