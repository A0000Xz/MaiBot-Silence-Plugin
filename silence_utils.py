from src.common.logger import get_logger
from typing import Optional, Dict, Any, Tuple
import hashlib
import os
import random
import toml
import traceback
import time

logger = get_logger("Silence")

class SilenceUtils:

    # 沉默状态记录
    _silence_records: Dict[str, Dict[str, Any]] = {} # 格式: {stream_id: {expiration: 过期时间戳 或 None}}, 如果过期时间戳是None表示永久沉默

    # 配置缓存
    _config_cache: Optional[Dict[str, Any]] = None
    _config_mtime: Optional[float] = None
    _last_mtime_check: float = 0

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
        max_action_silence_time = config["adjustment"]["max_action_silence_time"]
        
        if case == "low":
            duration = random.randint(low_min, low_max)  # 默认配置是2分钟到10分钟之间
        elif case == "medium":
            duration = random.randint(medium_min, medium_max) # 默认配置是10分钟到20分钟之间
        elif case == "serious":
            if duration is None:
                duration = random.randint(serious_min, serious_max)  # 默认配置是20分钟到一个半小时之间
            else:
                # 拒绝不合理的沉默时间
                if duration > max_action_silence_time:
                    return False
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

        # 直接存储计算好的时间戳
        cls._silence_records[stream_id] = {"expiration": expiration}
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
        
        # 已过期，直接删除记录
        del cls._silence_records[stream_id]
        logger.info(f"聊天流 {stream_id} 的沉默状态已过期，自动清理")
        return False, ""
    
    # 沉默人群检查方法
    @classmethod
    def is_silenced_someone(cls, user_id: int) -> Tuple[bool, str]:
        """沉默人群检查逻辑"""

        # 先读一下配置
        config = cls._load_config()
        enable = config.get("experimental", {}).get("silence_special_check", False)
        silence_someones = config.get("experimental", {}).get("silence_someone_list", [])
        
        # 检查用户ID是否在沉默人群列表中
        if not enable:
            return False, ""
        
        if not silence_someones:
            return False, ""
            
        if user_id in silence_someones:
            return True, "special_silence"
        
        else:
            return False, ""
        
    # 沉默群聊检查方法
    @classmethod
    def is_silenced_group(cls, group_id: int) -> Tuple[bool, str]:
        """沉默群聊检查逻辑"""

        # 先读一下配置
        config = cls._load_config()
        enable = config.get("experimental", {}).get("silence_special_check", False)
        silence_groups = config.get("experimental", {}).get("silence_group_list", [])
        
        # 检查群聊ID是否在沉默群聊列表中
        if not enable:
            return False, ""
        
        if not silence_groups:
            return False, ""
            
        if group_id in silence_groups:
            return True, "special_silence"
        
        else:
            return False, ""

    # 禁用command组件方法
    @classmethod
    def is_disable_commands(cls) -> Tuple[bool, list]:
        """检查是否禁用指令组件"""
        config = cls._load_config()
        return config["adjustment"]["disable_command"], ["silence_command"] + config["adjustment"]["unaffected_command_list"]
    
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
        
    # 检查是否启用沉默状态下表达学习
    @classmethod
    def check_expression_learning(cls) -> bool:
        """检查是否启用沉默状态下表达学习"""
        config = cls._load_config()
        return config.get("experimental", {}).get("silence_expression_learning", False)
    
    @staticmethod
    def generate_stream_id(platform: str, user_id: str, group_id: Optional[str]) -> str:
        """生成聊天流唯一ID（与ChatStream保持一致）"""
        if group_id:
            components = [platform, str(group_id)]
        else:
            components = [platform, str(user_id), "private"]
        
        key = "_".join(components)
        return hashlib.md5(key.encode()).hexdigest()

    @classmethod
    def _load_config(cls) -> Dict[str, Any]:
        """从配置文件加载配置（带缓存）"""
        current_time = time.time()
        
        # 有缓存且距离上次检查不到3秒，直接返回缓存
        if cls._config_cache is not None and current_time - cls._last_mtime_check < 3.0:
            return cls._config_cache
        
        cls._last_mtime_check = current_time
        
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, "config.toml")
            current_mtime = os.path.getmtime(config_path)
            
            # 文件未修改且有缓存，返回缓存
            if cls._config_mtime == current_mtime and cls._config_cache is not None:
                return cls._config_cache
            
            # 文件已修改，重新加载
            cls._config_mtime = current_mtime
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = toml.load(f)
            
            cls._config_cache = {
                "permissions": {
                    "white_or_black_list": config_data.get("permissions", {}).get("white_or_black_list", "whitelist"),
                    "admin_users": config_data.get("permissions", {}).get("admin_users", [])
                },
                "adjustment": {
                    "disable_command": config_data.get("adjustment", {}).get("disable_command", True),
                    "unaffected_command_list": config_data.get("adjustment", {}).get("unaffected_command_list", []),
                    "low_case": config_data.get("adjustment", {}).get("low_case", [120, 600]),
                    "medium_case": config_data.get("adjustment", {}).get("medium_case", [600, 1200]),
                    "serious_case": config_data.get("adjustment", {}).get("serious_case", [1200, 5400]),
                    "max_action_silence_time": config_data.get("adjustment", {}).get("max_action_silence_time", 10800)
                },
                "experimental": {
                    "silence_expression_learning": config_data.get("experimental", {}).get("silence_expression_learning", False),
                    "silence_special_check": config_data.get("experimental", {}).get("silence_special_check", False),
                    "silence_someone_list": config_data.get("experimental", {}).get("silence_someone_list", []),
                    "silence_group_list": config_data.get("experimental", {}).get("silence_group_list", [])
                }
            }
            
            return cls._config_cache
            
        except Exception as e:
            logger.error(f"加载配置文件时出错: {str(e)}\n{traceback.format_exc()}")
            if cls._config_cache is not None:
                return cls._config_cache
            raise