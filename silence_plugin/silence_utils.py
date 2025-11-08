from src.plugin_system.core import component_registry
from src.plugin_system.apis import component_manage_api, auto_talk_api
from src.plugin_system.base.component_types import ComponentType
from src.common.logger import get_logger
from typing import Optional, Dict, Any, Tuple
import os
import toml
import traceback
import random
import time

logger = get_logger("Silence")

class SilenceUtils:

    # 沉默状态记录
    _silence_records: Dict[str, Dict[str, Any]] = {} # 格式: {stream_id: {expiration: 过期时间戳 或 None, banned_command: 被禁用的指令(列表)}}, 如果过期时间戳是None表示永久沉默

    # 添加沉默状态方法
    @classmethod
    def add_silence(cls, case: str, duration: Optional[int], stream_id: str) -> bool:
        """
        添加沉默状态
        """
    
        # 每次调用施加沉默方法时实时读取配置（支持热更新）
        config = cls._load_config()
        low_min, low_max = config["adjustment"]["low_case"]
        medium_min, medium_max = config["adjustment"]["medium_case"]
        serious_min, serious_max = config["adjustment"]["serious_case"]
        disable_command = config["adjustment"]["disable_command"]
        
        if case == "low":
            duration = random.randint(low_min, low_max)  # 默认配置是2分钟到10分钟之间
        elif case == "medium":
            duration = random.randint(medium_min, medium_max) # 默认配置是10分钟到20分钟之间
        elif case == "serious":
            if duration is None:
                duration = random.randint(serious_min, serious_max)  # 默认配置是20分钟到一个半小时之间
        elif case == "command":
            pass  # 直接使用传入的duration
        else:
            logger.error(f"无效的沉默情况类型: {case},你接入的LLM很可能犯傻了") # 理论上极低概率出错，但还是写了以防万一
            return False
        
        # 计算沉默状态结束时的时间戳
        if duration is None:
            expiration = None  # 永久沉默
        else:
            expiration = time.time() + duration

        # 根据配置决定是否禁用其他命令组件
        banned_commands = []
        if disable_command:
            banned_commands = cls._disable_commands(stream_id)

        # 禁用主动发言
        probability_multiplier = auto_talk_api.get_question_probability_multiplier(stream_id)
        auto_talk_api.set_question_probability_multiplier(stream_id, 0.0)

        # 直接存储计算好的时间戳，被禁用的命令列表，和原始的主动发言概率乘数
        cls._silence_records[stream_id] = {"expiration": expiration, "banned_command": banned_commands, "original_probability_multiplier": probability_multiplier}
        duration_str = f"{duration}秒" if duration else "永久"
        logger.info(f"已添加聊天流 {stream_id} 到沉默列表，类型: {case}，持续时间: {duration_str}")

        return True
    
    # 移除沉默状态方法
    @classmethod
    def remove_silence(cls, stream_id: str) -> bool:
        """
        移除沉默状态
        返回: True=成功移除, False=本来就不在沉默中
        """
        if stream_id not in cls._silence_records:
            logger.warning(f"聊天流 {stream_id} 未处于沉默状态")
            return False
        
        # 恢复其他命令组件
        cls._enable_commands(stream_id)

        # 恢复主动发言概率乘数
        original_multiplier = cls._silence_records[stream_id].get("original_probability_multiplier", 0.0)
        auto_talk_api.set_question_probability_multiplier(stream_id, original_multiplier)
        
        # 直接删沉默状态记录
        del cls._silence_records[stream_id]

        logger.info(f"已移除聊天流 {stream_id} 的沉默状态")
        return True

    # 检查是否处于沉默状态方法
    @classmethod
    def is_silenced(cls, stream_id: str) -> Tuple[bool, str]:
        """
        检查指定聊天流是否处于沉默状态
        自动清理过期记录
        """
        # 不在记录里就返回False
        if stream_id not in cls._silence_records:
            return False, ""
        
        expiration = cls._silence_records[stream_id].get("expiration")
        
        # 永久沉默返回True
        if expiration is None:
            return True, "force_silence"
        
        # 检查沉默状态是否过期
        current_time = time.time()
        if expiration >= current_time:
            return True, ""  # 没到时间就还在沉默中
        
        # 已过期，直接删除记录并恢复命令组件
        cls._enable_commands(stream_id)
        del cls._silence_records[stream_id]
        logger.info(f"聊天流 {stream_id} 的沉默状态已过期，自动清理")
        return False, ""

    # 禁用command组件方法
    @classmethod
    def _disable_commands(cls, stream_id: str) -> list:
        """禁用所有Command组件（除了SilenceCommand）"""
        try:
            banned_commands = []
            # 获取所有已注册的Command
            all_commands = component_registry.get_enabled_components_by_type(ComponentType.COMMAND)
            
            # 简单计数
            disabled_count = 0

            for command_name in all_commands:
                
                # 跳过SilenceCommand（保留解除沉默的指令）
                if command_name == "silence_command":
                    continue
                
                # 记录被禁用的命令列表
                banned_commands.append(command_name)

                # 禁用其他所有Command
                component_manage_api.locally_disable_component(
                    command_name, 
                    ComponentType.COMMAND, 
                    stream_id
                )
                disabled_count += 1
            
            logger.info(f"已为聊天流 {stream_id} 禁用 {disabled_count} 个Command组件")

            # 返回被禁用的命令列表
            return banned_commands
        
        except Exception as e:
            logger.error(f"禁用Command组件时出错: {str(e)}\n{traceback.format_exc()}")
            return []

    # 恢复command组件方法
    @classmethod
    def _enable_commands(cls, stream_id: str):
        """恢复所有Command组件"""
        try:
            # 获取指定聊天流在沉默期间被禁用的命令列表
            banned_commands = cls._silence_records.get(stream_id, {}).get("banned_command", [])

            # 一个简单小计数
            enabled_count = 0

            for command_name in banned_commands:

                # 恢复所有Command
                component_manage_api.locally_enable_component(
                    command_name, 
                    ComponentType.COMMAND, 
                    stream_id
                )
                enabled_count += 1
            
            logger.info(f"已为聊天流 {stream_id} 恢复 {enabled_count} 个Command组件")

        except Exception as e:
            logger.error(f"恢复Command组件时出错: {str(e)}\n{traceback.format_exc()}")
    
    # 权限检查方法
    @classmethod
    def check_person_permission(cls, user_id: int) -> bool:
        """权限检查逻辑"""

        # 先读一下配置
        config = cls._load_config()
        mode = config.get("permissions", {}).get("white_or_black_list", "whitelist")
        admin_users = config.get("permissions", {}).get("admin_users", [])
        
        # 检查用户ID是否在管理员列表中
        if not admin_users:
            logger.info(f"未配置管理员用户列表")
            return False
        if mode == "whitelist":
            return user_id in admin_users
        else:
            return user_id not in admin_users

    # 读取配置方法
    @classmethod
    def _load_config(cls) -> Dict[str, Any]:
        """从同级目录的silence_config.toml文件直接加载配置"""
        try:
            # 获取当前文件所在目录
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, "silence_config.toml")
            
            # 读取并解析TOML配置文件
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = toml.load(f)
            
            # 构建配置字典，使用get方法安全访问嵌套值
            config = {
                "permissions": {
                    "white_or_black_list": config_data.get("permissions", {}).get("white_or_black_list", "whitelist"),
                    "admin_users": config_data.get("permissions", {}).get("admin_users", [])
                },
                "adjustment": {
                    "disable_command": config_data.get("adjustment", {}).get("disable_command", True),
                    "low_case": config_data.get("adjustment", {}).get("low_case", [120,600]),
                    "medium_case": config_data.get("adjustment", {}).get("medium_case", [600,1200]),
                    "serious_case": config_data.get("adjustment", {}).get("serious_case", [1200,5400])
                }
            }
            return config
        
        except Exception as e:
            logger.error(f"加载配置文件时出错: {str(e)}\n{traceback.format_exc()}")
            raise