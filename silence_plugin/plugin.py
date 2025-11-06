from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.apis.plugin_register_api import register_plugin
from src.plugin_system.base.base_action import BaseAction, ActionActivationType, ChatMode
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.config_types import ConfigField
from src.plugin_system.base.component_types import ComponentInfo, ComponentType
from src.config.official_configs import ChatConfig
from src.config.config import global_config
from src.plugin_system.apis import component_manage_api, message_api
from typing import Tuple, Optional, List, Type, Dict, Any
import traceback
import toml
import random
import os
import time
import re
import asyncio
from src.common.logger import get_logger
from plugins.silence_plugin.silence_core import SilenceCore
from plugins.silence_plugin import logger_patch
from src.plugin_system.apis import generator_api

logger = get_logger("Silence")

# 在ChatConfig类上设置属性来确保补丁只打一次
def apply_silence_patch_once():
    """确保沉默补丁只应用一次"""
    
    # 检查是否已经打过补丁
    if hasattr(ChatConfig, "_silence_patch_applied") and ChatConfig._silence_patch_applied:
        return
    
    # 保存原始方法
    original_method = ChatConfig.talk_frequency
    
    # 创建补丁方法
    def patched_method(self, chat_stream_id: Optional[str] = None) -> float:
        """补丁方法，对特定聊天流返回极低频率"""
        if chat_stream_id and SilenceCore.is_silenced(chat_stream_id):
            return 0.00000001  # 极低频率值
        return original_method(self, chat_stream_id)
    
    # 应用补丁
    ChatConfig.talk_frequency = patched_method
    ChatConfig._silence_patch_applied = True
    # logger.info("沉默补丁已应用")

def _load_config() -> Dict[str, Any]:
        """从同级目录的config.toml文件直接加载配置"""
        try:
            # 获取当前文件所在目录
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, "config.toml")
            
            # 读取并解析TOML配置文件
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = toml.load(f)
            
            # 构建配置字典，使用get方法安全访问嵌套值
            config = {
                "permissions": {
                    "admin_users": config_data.get("permissions", {}).get("admin_users", [])
                },
                "adjustment": {
                    "disable_command": config_data.get("adjustment", {}).get("disable_command", True)
                }
            }
            return config
        except Exception as e:
            logger.error(f"加载配置文件时出错: {str(e)}\n{traceback.format_exc()}")
            raise

def _get_components_to_disable() -> tuple[List[str], List[str]]:
        """获取需要禁用的组件列表"""
        try:
            # 获取启用的Action组件
            enabled_actions = component_manage_api.get_enabled_components_info_by_type(ComponentType.ACTION)
            # 获取启用的Command组件
            enabled_commands = component_manage_api.get_enabled_components_info_by_type(ComponentType.COMMAND)
            
            # 筛选出非silence相关的组件
            actions_to_disable = [name for name in enabled_actions.keys() if name != "silence_stop_action"]
            commands_to_disable = [name for name in enabled_commands.keys() if name != "silence_command"]

            config =_load_config()
            disable_command = config.get("adjustment", {}).get("disable_command", True)

            if not disable_command:
                commands_to_disable = []

            return actions_to_disable, commands_to_disable
        except Exception as e:
            logger.error(f"获取组件失败: {str(e)}\n{traceback.format_exc()}")
            return [], []

@register_plugin
class SilencePlugin(BasePlugin):
    """沉默插件"""
    
    # 插件基本信息
    plugin_name = "silence_plugin"
    enable_plugin = True
    config_file_name = "config.toml"
    dependencies = []
    python_dependencies = []

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件基本配置",
        "components": "组件启用控制",
        "permission": "命令组件的权限控制（支持热重载）",
        "adjustment": "功能微调（支持热重载，但仅在下一次沉默执行时生效）",
        "logging": "日志记录配置",
    }

    # 配置Schema定义
    config_schema = {
        "plugin": {
            "config_version": ConfigField(type=str, default="0.9.6", description="插件配置文件版本号"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
        },
        "components": {
            "enable_silence_action": ConfigField(type=bool, default=True, description="是否启用沉默的Action组件"),
            "enable_stop_silence_action": ConfigField(type=bool, default=False, description="是否启用中止沉默的Action组件，默认禁用为正常，请不要随意修改"),
            "enable_silence_command": ConfigField(type=bool, default=True, description="是否启用沉默的Command组件"),
        },
        "permissions": {
            "admin_users": ConfigField(type=List, default=["123456789"], description="请写入被许可用户的QQ号，记得用英文单引号包裹并使用逗号分隔。这个配置会决定谁被允许使用指令，注意，这个选项支持热重载（你可以不重启麦麦，改动会即刻生效）"),
        },
        "adjustment": {
            "disable_command": ConfigField(type=bool, default=True, description="是否令沉默插件连命令也保持沉默，默认为开"),
        },
        "logging": {
            "level": ConfigField(
                type=str, default="INFO", description="日志级别", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
            ),
            "prefix": ConfigField(type=str, default="[Silence]", description="日志前缀"),
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 初始化SilenceCore
        config_path = os.path.join(os.path.dirname(__file__), "silence_restrictions.json")
        SilenceCore.init(config_path)

        # 应用猴子补丁（确保只打一次）
        logger_patch.apply_logger_color_patch_once()
        apply_silence_patch_once()

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""
        components = []

        if self.get_config("components.enable_silence_action", True):
            components.append((SilenceAction.get_action_info(), SilenceAction))

        if not self.get_config("components.enable_stop_silence_action", False):
            components.append((SilenceStopAction.get_action_info(), SilenceStopAction))

        if self.get_config("components.enable_silence_command", True):
            components.append((SilenceCommand.get_command_info(), SilenceCommand))

        return components

class SilenceAction(BaseAction):
    action_name = "silence_action"
    focus_activation_type = ActionActivationType.ALWAYS
    normal_activation_type = ActionActivationType.ALWAYS
    activation_keywords = ["闭嘴" ]
    keyword_case_sensitive = False
    mode_enable = ChatMode.ALL
    parallel_action = False
    action_description = "根据当前聊天情况决定是否沉默不语"
    action_parameters = {
        "case": "让你决定执行这个动作的情况，必填，只能填一个参数。如果你觉得自己应该收敛一点，适当保持沉默，填'low'；如果你感觉聊天气氛不对劲，自己说错了话，或者参与聊天的人明显对你说话有意见甚至生气，隐约表达了需要你安静的意愿，填'medium'；如果你是被别人直接明确且礼貌地要求了保持沉默一段时间，填'serious'",
        "time": "沉默的时间长度，选填，必须填入以秒为单位的整数数字。如果是被人明确要求了保持沉默多久的话，把对方要求的时间长度换算成秒数填入即可；如果没有人对你明确要求沉默多久,请保持该参数为None"
    }
    action_require = [
        "当你觉得自己话太多了，且有人也隐约反映你说话太多时使用该动作。注意，若没有明确察觉到聊天气氛的恶化或者别人的反感时，请优先使用no_reply动作",
        "当聊天环境中有人明确表达了对你话多的不满，或者你说的话确实不合时宜或不够专业，具备误导性时，请使用此动作",
        "当聊天环境内有人明确且礼貌地要求你保持沉默一段时间时，使用此动作",
        "如果有人只是蛮横无理地要求你闭嘴，并带有侮辱性质的话，绝对不要使用这个动作！！！"
        "请注意，如果有用户使用了'/silence false'这条指令解开你的沉默状态时，短时间内不要再使用这个动作！！！"
    ]
    associated_types = ["text","emoji","image"]

    async def execute(self) -> Tuple[bool, str]:

        if not self.chat_stream.group_info:
            return False, "如果你看到这条消息就说明保险机制生效了，这个action理论上不应该作用于私聊，毕竟你的麦麦也不应该会主动话痨"

        # 获取当前聊天流ID
        stream_id = self.chat_stream.stream_id
        
        # 检查是否可以添加沉默
        if SilenceCore.is_silenced(stream_id):
            return False, f"聊天流 {stream_id} 已经处于沉默状态"
        
        # 根据情况确定持续时间
        case = self.action_data.get("case", "")
        duration = None  # 默认永久
        
        if case == "low":
            duration = random.randint(300, 600)  # 5分钟到10分钟之间
        elif case == "medium":
            duration = random.randint(600, 1800) # 10分钟到20分钟之间
        elif case == "serious":
            # 从参数中获取时间（如果提供）
            time_value = self.action_data.get("time", "")
            if time_value:
                try:
                    duration = float(time_value)
                except ValueError:
                    duration = random.randint(1800, 5400)  # 默认30分钟到一个半小时之间
        else:
            duration = random.randint(1800, 5400)  # 默认30分钟到一个半小时之间
        
        # 获取需要禁用的组件
        disabled_actions, disabled_commands = _get_components_to_disable()
        
        # 添加到沉默列表（Silence_Core会自动处理组件禁用）
        silenceOn = await SilenceCore.add_silence(False, self.message.chat_stream, stream_id, duration, disabled_actions, disabled_commands)
        if silenceOn:
            # 记录动作信息
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=f"已成功在聊天流{stream_id}进入沉默状态",
                action_done=True
                )

            return True, f"已对聊天流 {stream_id} 执行沉默操作"
        else:
            return False, f"聊天流 {stream_id} 已经在沉默列表里"

class SilenceStopAction(BaseAction):
    action_name = "silence_stop_action"
    focus_activation_type = ActionActivationType.ALWAYS
    normal_activation_type = ActionActivationType.ALWAYS
    activation_keywords = ["张嘴"]
    keyword_case_sensitive = False
    mode_enable = ChatMode.ALL
    parallel_action = False
    action_description = "根据当前聊天情况决定是否解除沉默"
    action_parameters = {}
    action_require = [
        "无论如何，当这个动作出现在你的可选列表里的时候，这意味着你进入了沉默保持状态，请一定要选择执行这个动作，因为它只会在你必须要用它的时候才被启用并出现在你的可选列表里！！！",
        "请注意！！！当这个动作出现在你的可选列表里的时候，一定要选择执行这个动作。绝对不要选择使用no_reply动作和reply动作！！！！！！"
    ]
    associated_types = ["text","emoji","image"]

    async def execute(self) -> Tuple[bool, str]:
        # 获取当前聊天流ID
        self_id = str(global_config.bot.qq_account)
        stream_id = self.chat_stream.stream_id
        start_time = time.time()
        mention_pattern = re.compile(rf'@<[^>]*:{re.escape(self_id)}>')

        logger.info("已进入沉默状态，开始等待...")

        while True:
            current_time = time.time()
            elapsed_time = current_time - start_time

            # 检查新消息
            recent_messages_dict = message_api.get_messages_by_time_in_chat(
                chat_id=self.chat_id,
                start_time=start_time,
                limit=15,
                end_time=current_time,
                filter_mai=True,
                filter_command=True,
            )

            mentioned_message = [msg for msg in recent_messages_dict if msg.get("is_mentioned")]

            for msg in mentioned_message:
                processed_text = msg.get("processed_plain_text", "")
                if processed_text and mention_pattern.search(processed_text):
                    # 移除沉默（这会自动处理组件恢复）
                    await SilenceCore.remove_silence(False, self.message.chat_stream, stream_id)
                    # 记录动作信息
                    await self.store_action_info(
                        action_build_into_prompt=True,
                        action_prompt_display=f"检测到艾特打断，已成功在聊天流{stream_id}解除沉默状态",
                        action_done=True
                        )
                    return True, f"检测到艾特自身的消息，解除聊天流 {stream_id} 的沉默状态"
                
            if not SilenceCore.is_silenced(stream_id):
                # 记录动作信息
                await self.store_action_info(
                    action_build_into_prompt=True,
                    action_prompt_display=f"检测到时间已到，已成功在聊天流{stream_id}解除沉默状态",
                    action_done=True
                    )
                return True, f"检测到沉默状态已过期，解除聊天流 {stream_id} 的沉默状态"

            # 每60秒输出一次等待状态
            if int(elapsed_time) > 0 and int(elapsed_time) % 60 == 0:
                logger.debug(
                    f"{self.log_prefix}已沉默{elapsed_time:.0f}秒，继续保持沉默..."
                )
            await asyncio.sleep(1)

class SilenceCommand(BaseCommand):
    command_name = "silence_command"
    command_description = "沉默插件"
    command_pattern = r"^/silence\s+(?P<action>\w+)(?:\s+(?P<duration>\d+))?\s*$"
    command_help = "使用'/silence true [持续时间]'执行沉默，'/silence false'结束沉默"
    command_examples = ["/silence true [times]", "/silence false"]

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        sender = self.message.message_info.user_info

        if not self._check_person_permission(sender.user_id):
            success, reply_set, prompt = await generator_api.rewrite_reply(
                chat_stream=self.chat_stream,
                raw_reply="你谁啊就随便禁言我？",
                reason="用户试图禁言你，但没有权限没有成功，表示好奇，一句话，必须回复内容",
                return_prompt=True
            )
            if success and reply_set:
                for reply_type, reply_content in reply_set:
                    if reply_type == "text":
                        await self.send_text(reply_content)
            return False, "权限不足，无权使用此命令", True
        
        if not self.message.message_info.group_info:
            logger.info("你为什么要在私聊环境使用沉默插件的指令？")
            return False, "该命令不应该用于私聊环境", True
        
        action = self.matched_groups.get("action", "")
        duration = self.matched_groups.get("duration")
        stream_id = self.message.chat_stream.stream_id
        
        if action == "true":
            # 检查是否可以添加沉默
            if SilenceCore.is_silenced(stream_id):
                return True, f"聊天流 {stream_id} 已经处于沉默状态", True
            
            # 获取需要禁用的组件
            disabled_actions, disabled_commands = _get_components_to_disable()
            
            duration_val = float(duration) if duration else None
            silenceOn = await SilenceCore.add_silence(True, self.message.chat_stream, stream_id, duration_val, disabled_actions, disabled_commands)
            if silenceOn:
                return True, f"已添加聊天流 {stream_id} 到沉默列表", True
            else:
                return True, f"聊天流 {stream_id} 添加到沉默列表失败", True
        
        elif action == "false":
            # 检查是否可以移除沉默（这会自动处理组件恢复）
            silenceOff = await SilenceCore.remove_silence(True, self.message.chat_stream, stream_id)
            if silenceOff:
                return True, f"已从沉默列表移除聊天流 {stream_id}", True
            else:
                return True, f"从沉默列表移除聊天流 {stream_id} 失败", True
    
    def _check_person_permission(self, user_id: str) -> bool:
        """权限检查逻辑"""
        config = _load_config()
        admin_users = config.get("permissions", {}).get("admin_users", [])
        if not admin_users:
            logger.warning(f"未配置管理员用户列表")
            return False
        return user_id in admin_users
