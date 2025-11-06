from src.common.logger import get_logger
from src.config.official_configs import ChatConfig
from .silence_utils import SilenceUtils
from typing import Optional

logger = get_logger("Silence")

# 沉默补丁方法
def apply_silence_patch_once():
    """确保沉默补丁只应用一次"""
    
    # 检查是否已经打过补丁
    if hasattr(ChatConfig, "_silence_patch_applied") and ChatConfig._silence_patch_applied:
        return
    
    try:
        # 保存原始方法
        original_method = ChatConfig.get_talk_value
        
        # 创建补丁方法
        def patched_method(self, chat_id: Optional[str]) -> float:
            """补丁方法，对特定聊天流返回极低频率"""

            # 检查是否处于沉默状态
            is_silenced, silence_reason = SilenceUtils.is_silenced(chat_id)
            if is_silenced:
                # logger.info(f"补丁成功生效！ 聊天流 {chat_id} 处于沉默状态，返回0.0发言频率")

                # 是的话就返回0.0
                return 0.0
            
            # 否则调用原始方法
            return original_method(self, chat_id)
        
        # 应用补丁
        ChatConfig.get_talk_value = patched_method
        ChatConfig._silence_patch_applied = True
        # logger.info("沉默补丁已成功应用")

    except Exception as e:
        logger.error(f"应用沉默补丁失败: {str(e)}")