from src.person_info.person_info import Person
from src.common.database.database_model import Images
from src.common.logger import MODULE_ALIASES, MODULE_COLORS, get_logger
from src.chat.message_receive.chat_stream import get_chat_manager
from src.chat.utils.utils import is_mentioned_bot_in_message
from src.chat.utils.chat_message_builder import replace_user_references
from src.chat.message_receive.storage import MessageStorage
from src.plugin_system.apis.plugin_register_api import register_plugin
from src.plugin_system.base.base_plugin import BasePlugin
from src.plugin_system.base.base_action import BaseAction, ActionActivationType
from src.plugin_system.base.base_command import BaseCommand
from src.plugin_system.base.base_events_handler import BaseEventHandler
from src.plugin_system.base.config_types import ConfigField
from src.plugin_system.base.component_types import ComponentInfo, EventType
from .silence_utils import SilenceUtils
from .mute_utils import MuteUtils
from typing import List, Tuple, Type, Optional
import re

MODULE_ALIASES["Silence"] = "沉默插件" # 定义插件的日志前缀名
MODULE_ALIASES["Silence_Save"] = "所见" # 特殊的保存日志前缀名
MODULE_COLORS["Silence"] = "\033[38;5;27m" # 浅蓝色
MODULE_COLORS["Silence_Save"] = "\033[38;5;82m" # 亮蓝色
logger = get_logger("Silence") # 正式创建日志实例
logger_save = get_logger("Silence_Save") # 一个特殊的保存日志实例

@register_plugin
class SilencePlugin(BasePlugin):
    """
    沉默插件
    - 在合适的时候让麦麦保持沉默，不进行任何回复
    """

    # 插件信息
    plugin_name = "silence_plugin" # 插件名称
    enable_plugin = True # 是否启用插件
    dependencies = []  # 依赖的其他插件列表
    python_dependencies = []  # 依赖的Python包列表
    config_file_name = "silence_config.toml"  # 配置文件名称

    # 配置节描述
    config_section_descriptions = {
        "plugin": "插件基本配置",
        "components": "插件组件开关配置",
        "permissions": "权限配置(内部设置均可热重载)",
        "adjustment": "沉默的个性化调整(内部设置均可热重载)",
        "experimental": "实验性功能（内部设置均可热重载）"
    } 

    # 配置Schema定义
    config_schema = {
        "plugin": {
            "config_version": ConfigField(type=str, default="1.6.0", description="插件配置文件版本号"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件（总开关）")
        },
        "components": {
            "enable_silence_action": ConfigField(type=bool, default=True, description="是否启用沉默动作组件"),
            "enable_silence_command": ConfigField(type=bool, default=True, description="是否启用沉默命令组件"),
            "enable_silence_event_handler": ConfigField(type=bool, default=True, description="是否启用沉默事件处理器组件")
        },
        "permissions": {
            "white_or_black_list": ConfigField(type=str, default="whitelist", description="管理用户列表的类型，支持'whitelist'（白名单）和'blacklist'（黑名单）两种模式"),
            "admin_users": ConfigField(type=list, default=[123456789,], description="能够使用沉默命令的用户QQ号列表")
        },
        "adjustment": {
            "disable_command": ConfigField(type=bool, default=True, description="是否禁用其他命令组件"),
            "low_case": ConfigField(type=list, default=[120,600], description="低级别沉默时间范围，单位为秒，格式为[min,max]"),
            "medium_case": ConfigField(type=list, default=[600,1200], description="中级别沉默时间范围，单位为秒，格式为[min,max]"),
            "serious_case": ConfigField(type=list, default=[1200,5400], description="高级别沉默时间范围，单位为秒，格式为[min,max]"),
            "max_action_silence_time": ConfigField(type=int, default=10800, description="通过动作触发的最大沉默时间，单位为秒，超过该时间将被强制打回，避免被人滥用")
        },
        "experimental": {
            "silence_someone_check": ConfigField(type=bool, default=False, description="启用针对特定用户的沉默检查功能（实验性功能）"),
            "silence_someone_list": ConfigField(type=list, default=[123456789,], description="被沉默检查的用户ID列表，仅在启用沉默检查功能时生效")
        }
    }
    # manifest文件名
    manifest_file_name: str = "manifest.json"  

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件包含的组件列表"""
        components = []
        if self.get_config("components.enable_silence_action", True):
            components.append((SilenceAction.get_action_info(), SilenceAction))
        if self.get_config("components.enable_silence_command", True):
            components.append((SilenceCommand.get_command_info(), SilenceCommand))
        if self.get_config("components.enable_silence_event_handler", True):
            components.append((SilenceEventHandler.get_handler_info(), SilenceEventHandler))
        return components
    
class SilenceAction(BaseAction):
    """
    沉默动作
    -在合适的情况下让麦麦选择进入沉默状态
    -例如检测到群聊的人们开始反感麦麦说话
    -例如检测到用户明确要求麦麦保持沉默
    """
    # 沉默动作的基本信息
    action_name = "silence"

    # 一些必要的初始化
    activation_type = ActionActivationType.ALWAYS # 始终激活

    # 模式和并行控制
    parallel_action = False # 不允许并行

    # 动作的描述信息
    action_description = "根据当前聊天的情况判断自己是否应该选择进入沉默状态" 
    action_parameters = {
    "case": "让你决定执行这个动作的情况，必填，只能填一个参数。如果你觉得自己应该收敛一点，适当保持沉默，填'low'；如果你感觉聊天气氛不对劲，自己说错了话，或者参与聊天的人显著对你说话有意见甚至生气，隐约表达了需要你安静的意愿，填'medium'；如果你是被别人直接明确且礼貌地要求了保持沉默一段时间，填'serious'",
    "time": "沉默的时间长度，选填，必须填入以秒为单位的整数数字。如果被人明确要求了保持沉默多久的话，把对方要求的时间长度换算成秒数填入即可；如果没有人对你明确要求沉默多久,请一定要保持该参数为None，绝对不要填入数字！"
    }
    action_require = [
    "当你觉得自己话太多了，同时有人也明确反映你说话太多时使用该动作",
    "当聊天环境中有人明确表达了对你话多的不满，或者你说的话确实不合时宜或不够专业，具备误导性时，请使用此动作",
    "当聊天环境内有人明确且礼貌地要求你保持沉默一段时间时，使用此动作",
    "如果有人只是蛮横无理地要求你闭嘴，并对你说了带有侮辱性质的话，绝对不要使用这个动作，你必须维护你自己的尊严！！！",
    "请注意，如果有用户使用了'/silence false'这条指令解除你的沉默状态时，短时间内不要再使用这个动作！！！"
    ]
    associated_types = ["text","emoji","image"] # 该动作会回应的消息类型

    async def execute(self) -> Tuple[bool, str]:

        if not self.chat_stream.group_info:
            logger.info("如果你看到这条消息就说明保险机制生效了，这个action理论上不应该作用于私聊，毕竟你的麦麦也不应该会主动说话")
            return False, "如果你看到这条消息就说明保险机制生效了，这个action理论上不应该作用于私聊，毕竟你的麦麦也不应该会主动说话"

        # 获取当前聊天流ID
        stream_id = self.chat_stream.stream_id
        
        # 检查是否可以添加沉默
        is_silenced, _ = SilenceUtils.is_silenced(stream_id)
        if is_silenced:
            return False, f"聊天流 {stream_id} 已经处于沉默状态"
        
        # 从参数中获取情况类型
        case = self.action_data.get("case", "")

        # 从参数中获取沉默时间
        duration = self.action_data.get("time") 
        
        # 添加到沉默列表
        if SilenceUtils.add_silence(case, duration, stream_id):

            # 记录动作信息
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=f"已成功在聊天流{stream_id}进入沉默状态",
                action_done=True
                )
            
            return True, f"已对聊天流 {stream_id} 执行沉默操作"
        else:
            return False, f"聊天流 {stream_id} 已经在沉默列表里"
        
class SilenceCommand(BaseCommand):
    """
    沉默命令
    -允许管理员用户手动让麦麦进入沉默状态，或解除沉默状态
    -可以指定沉默时长，不指定即为永久沉默
    """
    # 命令名
    command_name: str = "silence_command"

    # 命令描述
    command_description: str = "沉默插件命令模块"
    
    # 命令匹配正则表达式
    command_pattern: str = r"^/silence\s+(?P<action>\w+)(?:\s+(?P<duration>\d+))?\s*$"

    # 命令执行总函数
    async def execute(self) -> Tuple[bool, Optional[str], bool]:

        # 确定发送者ID（传进来的是str，因此要转换成int）
        sender_id = int(self.message.message_info.user_info.user_id)

        # 使用权限检查
        if not SilenceUtils.check_person_permission(sender_id):
            await self.send_text("权限不足，你无权使用此命令")    
            return True, "权限不足，无权使用此命令", True
         
        # 私聊环境检查
        if not self.message.message_info.group_info:
            logger.info("你为什么要在私聊环境使用沉默插件的指令？")
            return True, "该命令不应该用于私聊环境", True
        
        # 解析命令需要参数
        action = self.matched_groups.get("action", "")
        duration = self.matched_groups.get("duration")
        stream_id = self.message.chat_stream.stream_id
        duration_val = float(duration) if duration else None
        case = "command"
        
        # 添加沉默状态的分支
        if action == "true":

            # 检查是否可以添加沉默
            is_silenced, _ = SilenceUtils.is_silenced(stream_id)

            # 如果已经沉默了，先移除再添加（直接覆盖已有的）
            if is_silenced:
                SilenceUtils.remove_silence(stream_id) 

            # 交给SilenceUtils干活咯
            if SilenceUtils.add_silence(case, duration_val, stream_id):
                return True, f"已添加聊天流 {stream_id} 到沉默列表", True
            else:
                return True, f"聊天流 {stream_id} 沉默失败了", True
        
        # 移除沉默状态的分支
        elif action == "false":

            # 交给SilenceUtils干活咯
            if SilenceUtils.remove_silence(stream_id):
                return True, f"已从沉默列表移除聊天流 {stream_id}", True
            else:
                return True, f"聊天流 {stream_id}并不处于沉默状态中，或者移除失败了 ", True
            
class SilenceEventHandler(BaseEventHandler):
    """
    沉默事件处理器
    -监听ON_MESSAGE事件，确保截断回复
    """
    # 事件类型
    event_type = EventType.ON_MESSAGE
    
    # 处理器名称
    handler_name = "silence_event_handler"
    
    # 处理器描述
    handler_description = "沉默事件处理器"
    
    # 处理器权重
    weight = 999
    
    # 是否阻塞消息（消息的处理流程到底等不等这个处理器忙活完）
    intercept_message = True

    async def execute(self, message):
       
        # 获取当前聊天流ID和相关需要信息
        stream_id = message.stream_id
        user_id = message.message_base_info.get("user_id")

        # 先进行一次禁言状态检查更新
        MuteUtils.mute_check(message) 

        # 检查是否处于沉默状态
        is_silenced, silence_reason = SilenceUtils.is_silenced(stream_id)

        # 进行针对特定用户的沉默检查（实验性功能）
        if not is_silenced and user_id:
            is_silenced, silence_reason = SilenceUtils.is_silenced_someone(int(user_id))

        # 如果处于沉默或被禁言状态，则截断回复流程
        if is_silenced or MuteUtils.is_muted(stream_id):

            # 获取原始消息(MessageRecv对象)
            chat_stream = get_chat_manager().get_stream(message.stream_id)
            original_message = chat_stream.context.get_last_message()

            # 计算at信息等
            is_mentioned, is_at, reply_probability_boost = is_mentioned_bot_in_message(original_message)

            # 处理被at的特殊情况，解除沉默状态
            if is_at and silence_reason not in ["force_silence", "user_silence"]:
                logger.info(f"检测到在沉默状态下被at，已解除聊天流 {stream_id} 的沉默状态")
                SilenceUtils.remove_silence(stream_id)
                return True, True, None, None, None  # 成功执行，允许后续处理
            
            if silence_reason == "force_silence" and is_at:
                logger.info(f"该沉默为指令强行指定的永久沉默，艾特无法打断")
            
            # 走一下自定义的消息预加工流程
            userinfo = original_message.message_info.user_info
            chat = original_message.chat_stream
            original_message.is_mentioned = is_mentioned
            original_message.is_at = is_at
            original_message.intercept_message_level = 1
            original_message.reply_probability_boost = reply_probability_boost
            mes_name = chat.group_info.group_name if chat.group_info else "私聊"

            # 存储消息
            await MessageStorage.store_message(original_message, chat)

            # 用这个pattern截取出id部分，picid是一个list，并替换成对应的图片描述
            picid_pattern = r"\[picid:([^\]]+)\]"
            picid_list = re.findall(picid_pattern, original_message.processed_plain_text)

            # 创建替换后的文本
            processed_text = original_message.processed_plain_text
            if picid_list:
                for picid in picid_list:
                    image = Images.get_or_none(Images.image_id == picid)
                    if image and image.description:
                        # 将[picid:xxxx]替换成图片描述
                        processed_text = processed_text.replace(f"[picid:{picid}]", f"[图片：{image.description}]")
                    else:
                        # 如果没有找到图片描述，则移除[picid:xxxx]标记
                        processed_text = processed_text.replace(f"[picid:{picid}]", "[图片：网络不好，图片无法加载]")

            # 应用用户引用格式替换，将回复<aaa:bbb>和@<aaa:bbb>格式转换为可读格式
            processed_plain_text = replace_user_references(
                processed_text,
                original_message.message_info.platform,  # type: ignore
                replace_bot_name=True,
            )

            logger_save.info(f"[{mes_name}](沉默中，已记录){userinfo.user_nickname}:{processed_plain_text}")  # type: ignore

            # 确保用户信息已注册
            _ = Person.register_person(
                platform=original_message.message_info.platform,  # type: ignore
                user_id=original_message.message_info.user_info.user_id,  # type: ignore
                nickname=userinfo.user_nickname,  # type: ignore
            )

            return True, False, None, None, None  # 成功执行，阻止后续处理，且不返回任何消息
        else:
            return True, True, None, None, None  # 成功执行，允许后续处理