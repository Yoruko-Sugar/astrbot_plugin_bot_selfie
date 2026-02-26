import asyncio
import datetime
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image as AstrImage
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .core.config import ConfigLoader
from .core.rate_limiter import RateLimiter
from .core.api_client import ApiClient


@dataclass
class BotSelfieTool(FunctionTool[AstrAgentContext]):
    """
    Bot 自拍生成工具
    
    当用户请求 Bot 自拍、拍照时调用此函数。
    工具会立即返回确认信息，自拍照在后台生成完成后自动发送。
    """
    
    name: str = "bot_selfie_generation"
    handler_module_path: str = "astrbot_plugin_bot_selfie"
    description: str = (
        "生成 Bot 的自拍照片。"
        "当用户请求 Bot 自拍、拍照、发自拍等时调用此函数。"
        "此工具会立即返回确认，自拍照会在后台生成完成后自动发送给用户。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "outfit": {
                    "type": "string",
                    "description": "Bot 的穿搭描述，如果用户没有指定则留空使用今日穿搭",
                }
            },
            "required": [],
        }
    )
    
    # 插件实例引用
    plugin: Any = Field(default=None, repr=False)
    
    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        """执行自拍生成工具"""
        outfit = kwargs.get("outfit", "").strip()
        
        event = context.context.event
        plugin = self.plugin
        
        if not plugin:
            return "❌ 工具未正确初始化，缺少插件实例引用"
        
        # 检查限流
        if plugin.cfg.rate_limit_enabled:
            user_id = plugin._get_user_id(event)
            allowed, message = plugin.rate_limiter.check_and_consume(user_id)
            if not allowed:
                return message
        
        # 如果没有指定穿搭，获取今日穿搭
        if not outfit:
            outfit = await plugin._get_today_outfit(event)
            if not outfit:
                outfit = "休闲装"
        
        logger.info(f"[TOOL] 启动自拍生成任务: outfit={outfit}")
        
        # 启动后台任务
        gen_task = asyncio.create_task(
            _background_generate_selfie(
                plugin=plugin,
                event=event,
                outfit=outfit
            )
        )
        gen_task.add_done_callback(
            lambda t: t.exception()
            and logger.error(f"自拍生成后台任务异常: {t.exception()}")
        )
        
        # 返回给 AI 的提示信息
        return (
            f"[自拍生成任务已启动]（穿搭：{outfit}）\n"
            "自拍照正在生成中，通常需要 10-30 秒，生成完成后会自动发送给用户。\n"
            "请用你的人设告诉用户：正在拍照，马上就好，完成后会自动发送。"
        )


async def _background_generate_selfie(
    plugin: "BotSelfiePlugin",
    event: AstrMessageEvent,
    outfit: str
) -> None:
    """后台执行自拍生成并发送结果"""
    try:
        logger.debug("[TOOL-BG] 开始后台自拍生成...")
        
        # 生成自拍
        result = await plugin._generate_selfie(event, outfit)
        
        # 发送结果
        if result.startswith("❌"):
            await event.send(event.plain_result(result))
        else:
            await event.send(event.image_result(result))
        
        logger.info("[TOOL-BG] 自拍生成成功并已发送")
        
    except Exception as e:
        logger.error(f"[TOOL-BG] 后台自拍生成异常: {e}", exc_info=True)
        try:
            await event.send(event.plain_result(f"❌ 自拍生成失败：{str(e)}"))
        except Exception as send_error:
            logger.warning(f"[TOOL-BG] 发送异常消息失败: {send_error}")


class BotSelfiePlugin(Star):
    def __init__(self, context: Context, config: Dict[str, Any]):
        super().__init__(context)
        self.context = context
        self.raw_config = config
        
        # 加载配置
        self.cfg = ConfigLoader(config)
        
        # 初始化 API 客户端
        self.api_client = ApiClient(
            api_keys=self.cfg.api_keys,
            api_base=self.cfg.api_base,
            endpoint_id=self.cfg.endpoint_id
        )
        
        # 检查 API 密钥是否配置
        if not self.cfg.api_keys:
            logger.warning("API 密钥未配置，请在插件设置中配置至少一个 API 密钥")
        
        # 初始化限流器
        self.rate_limiter = RateLimiter(
            max_requests=self.cfg.rate_limit_max_requests,
            period_seconds=self.cfg.rate_limit_period
        )
        
        # 缓存今日穿搭
        self.today_outfit: Optional[str] = None
        self.last_update_date: Optional[str] = None
        
        # 注册 LLM 工具
        self._register_llm_tools()

    def _register_llm_tools(self):
        """注册 LLM 工具到 Context"""
        try:
            tool = BotSelfieTool(plugin=self)
            self.context.add_llm_tools(tool)
            logger.debug("已注册 BotSelfieTool 到 LLM 工具列表")
        except Exception as e:
            logger.warning(f"注册 LLM 工具失败: {e}")

    async def initialize(self):
        logger.info("🤳 Bot Selfie 插件已初始化")

    async def terminate(self):
        """插件卸载时清理"""
        if self.api_client:
            await self.api_client.close()
        logger.info("🤳 Bot Selfie 插件已卸载")
    
    def _get_user_id(self, event: AstrMessageEvent) -> str:
        """获取用户标识"""
        user_id = "unknown"
        try:
            if hasattr(event, 'user_id'):
                user_id = event.user_id or "unknown"
            elif hasattr(event, 'sender') and event.sender:
                sender = event.sender
                if hasattr(sender, 'user_id'):
                    user_id = sender.user_id or "unknown"
                elif hasattr(sender, 'id'):
                    user_id = sender.id or "unknown"
            elif hasattr(event, 'message_obj') and event.message_obj:
                message_obj = event.message_obj
                if hasattr(message_obj, 'user_id'):
                    user_id = message_obj.user_id or "unknown"
                elif hasattr(message_obj, 'sender') and message_obj.sender:
                    sender = message_obj.sender
                    if hasattr(sender, 'user_id'):
                        user_id = sender.user_id or "unknown"
                    elif hasattr(sender, 'id'):
                        user_id = sender.id or "unknown"
        except Exception as e:
            logger.warning(f"获取用户标识失败: {e}")
        return user_id

    async def _get_today_outfit(self, event: AstrMessageEvent) -> Optional[str]:
        """获取今日穿搭"""
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # 如果缓存有效，直接返回
        if self.last_update_date == today and self.today_outfit:
            return self.today_outfit
        
        # 尝试从 life_scheduler 插件获取今日穿搭
        try:
            # 尝试通过 context 获取 life_scheduler 插件
            life_scheduler_plugin = None
            
            # 方法1: 使用 get_registered_star
            try:
                life_scheduler_plugin = self.context.get_registered_star("astrbot_plugin_life_scheduler")
                if life_scheduler_plugin:
                    logger.info("通过 get_registered_star 找到 life_scheduler 插件")
            except Exception as e:
                logger.warning(f"使用 get_registered_star 获取插件失败: {e}")
            
            # 方法2: 遍历所有插件
            if not life_scheduler_plugin:
                try:
                    all_stars = self.context.get_all_stars()
                    for star in all_stars:
                        logger.info(f"发现插件: {star.name}")
                        if star.name == "astrbot_plugin_life_scheduler":
                            life_scheduler_plugin = star
                            logger.info("通过遍历找到 life_scheduler 插件")
                            break
                except Exception as e:
                    logger.warning(f"遍历插件列表失败: {e}")
            
            # 方法3: 尝试通过插件管理器获取
            if not life_scheduler_plugin:
                try:
                    plugin_manager = getattr(self.context, "_star_manager", None)
                    if plugin_manager:
                        # 检查是否有 get_plugin 或类似方法
                        if hasattr(plugin_manager, "get_plugin"):
                            life_scheduler_plugin = plugin_manager.get_plugin("astrbot_plugin_life_scheduler")
                        # 或者直接访问 plugins 属性
                        elif hasattr(plugin_manager, "plugins"):
                            for plugin in plugin_manager.plugins:
                                plugin_name = getattr(plugin, "name", "") or getattr(plugin, "plugin_name", "")
                                if plugin_name == "astrbot_plugin_life_scheduler":
                                    life_scheduler_plugin = plugin
                                    break
                        logger.info("通过插件管理器找到 life_scheduler 插件")
                except Exception as e:
                    logger.warning(f"通过插件管理器获取插件失败: {e}")
            
            if not life_scheduler_plugin:
                logger.warning("未找到 life_scheduler 插件")
                return None
            
            # 获取插件实例
            plugin_instance = None
            try:
                # 尝试获取插件实例
                if hasattr(life_scheduler_plugin, "instance"):
                    plugin_instance = life_scheduler_plugin.instance
                elif hasattr(life_scheduler_plugin, "star_cls"):
                    plugin_instance = life_scheduler_plugin.star_cls
                else:
                    # 如果是实例本身
                    plugin_instance = life_scheduler_plugin
                
                logger.info(f"获取到 life_scheduler 插件实例: {type(plugin_instance).__name__}")
            except Exception as e:
                logger.warning(f"获取插件实例失败: {e}")
                return None
            
            # 获取今日数据
            today_date = datetime.datetime.now()
            data = None
            try:
                # 尝试获取 data_mgr 属性
                data_mgr = getattr(plugin_instance, "data_mgr", None)
                if data_mgr and hasattr(data_mgr, "get"):
                    data = data_mgr.get(today_date)
                    logger.info("通过 data_mgr 获取今日数据")
            except Exception as e:
                logger.warning(f"获取今日数据失败: {e}")
            
            if not data:
                # 如果没有数据，尝试生成
                try:
                    # 尝试获取 unified_msg_origin
                    umo = None
                    try:
                        umo = event.unified_msg_origin
                    except Exception as e:
                        logger.warning(f"获取 unified_msg_origin 失败: {e}")
                    
                    # 尝试生成日程
                    # 尝试多种可能的路径访问 generate_schedule
                    data = None
                    if hasattr(plugin_instance, "generate_schedule"):
                        data = await plugin_instance.generate_schedule(today_date, umo)
                    elif hasattr(plugin_instance, "generator"):
                        generator = plugin_instance.generator
                        if generator and hasattr(generator, "generate_schedule"):
                            data = await generator.generate_schedule(today_date, umo)
                        else:
                            logger.warning("插件实例的 generator 属性中未找到 generate_schedule 方法")
                    else:
                        logger.warning("插件实例中未找到 generate_schedule 方法")
                    
                    if data:
                        logger.info("生成今日日程数据成功")
                    else:
                        logger.warning("生成今日日程数据失败")
                except Exception as e:
                    logger.error(f"生成日程失败: {e}")
            
            # 检查数据结构
            if data:
                # 尝试获取 outfit 属性
                outfit = None
                try:
                    if hasattr(data, "outfit"):
                        outfit = data.outfit
                    elif isinstance(data, dict) and "outfit" in data:
                        outfit = data["outfit"]
                    
                    if outfit:
                        self.today_outfit = outfit
                        self.last_update_date = today
                        logger.info(f"获取到今日穿搭: {outfit}")
                        return outfit
                    else:
                        logger.warning("今日穿搭数据为空")
                except Exception as e:
                    logger.warning(f"解析今日穿搭数据失败: {e}")
            
        except Exception as e:
            logger.error(f"获取今日穿搭失败: {e}")
        
        return None

    async def _generate_selfie(self, event: AstrMessageEvent, outfit: str) -> str:
        """生成自拍"""
        # 获取参考图路径
        reference_image = None
        if self.cfg.persona_reference_image:
            # 记录原始配置值
            logger.info(f"配置的参考图片: {self.cfg.persona_reference_image}")
            
            # 尝试获取插件数据目录
            plugin_data_dir = None
            try:
                from astrbot.api.star import StarTools
                plugin_data_dir = StarTools.get_data_dir()
                logger.info(f"插件数据目录: {plugin_data_dir}")
            except Exception as e:
                logger.warning(f"获取插件数据目录失败: {e}")
            
            # 如果是列表，取第一个元素
            if isinstance(self.cfg.persona_reference_image, list) and self.cfg.persona_reference_image:
                # 遍历列表，找到第一个有效的图片路径
                for i, img_path in enumerate(self.cfg.persona_reference_image):
                    logger.info(f"检查参考图片 {i}: {img_path}")
                    if img_path:
                        # 确保路径是字符串
                        if isinstance(img_path, str):
                            # 处理路径
                            check_path = img_path
                            # 如果是相对路径，尝试使用插件数据目录
                            if not os.path.isabs(img_path) and plugin_data_dir:
                                check_path = os.path.join(plugin_data_dir, img_path)
                                logger.info(f"转换为绝对路径: {check_path}")
                            # 检查路径是否存在
                            if os.path.exists(check_path):
                                reference_image = check_path
                                logger.info(f"找到有效的参考图片: {reference_image}")
                                break
                            else:
                                logger.warning(f"参考图片不存在: {check_path}")
                        else:
                            logger.warning(f"参考图片路径不是字符串: {type(img_path)}")
            elif isinstance(self.cfg.persona_reference_image, str):
                img_path = self.cfg.persona_reference_image
                logger.info(f"检查参考图片: {img_path}")
                # 处理路径
                check_path = img_path
                # 如果是相对路径，尝试使用插件数据目录
                if not os.path.isabs(img_path) and plugin_data_dir:
                    check_path = os.path.join(plugin_data_dir, img_path)
                    logger.info(f"转换为绝对路径: {check_path}")
                if os.path.exists(check_path):
                    reference_image = check_path
                    logger.info(f"找到有效的参考图片: {reference_image}")
                else:
                    logger.warning(f"参考图片不存在: {check_path}")
            else:
                logger.warning(f"参考图片配置格式错误: {type(self.cfg.persona_reference_image)}")
        else:
            logger.warning("未配置参考图片")
        
        # 构建提示词
        # 基于参考图风格调整提示词，确保与参考图风格一致
        if reference_image:
            prompt = f"把参考图片中的二次元人物形象改为一张自拍照片，可以是拿着手机对镜子自拍的视角，也可以是手机摄像头的视角，请注意一定要保持参考图片中的风格，且自拍照片中的人物形象（脸部细节、身材细节）务必和参考图片中的形象保持一致。背景可以根据下述的详细内容自由发挥，请注意不能是空白背景，请尽可能自由发挥，使整张图片的人物和背景比较协调。如下是详细的穿衣风格内容，请遵守上述规则，在仅改变人物动作和衣服风格的条件下进行改图：{outfit}"
        else:
            prompt = f"生成一张Bot的自拍照片，穿着：{outfit}。风格为二次元动漫风格，线条清晰，色彩鲜明，光线良好，背景简洁。"
        # 记录最终使用的参考图片路径
        if reference_image:
            logger.info(f"最终使用参考图片: {reference_image}")
        else:
            logger.warning("未找到有效的参考图片，将不使用参考图片")
        
        # 调用API生成图像
        # 使用 default_size 作为分辨率
        resolution = self.cfg.default_size or self.cfg.resolution
        logger.info(f"生成图像参数: prompt='{prompt}', resolution='{resolution}', reference_image={'已提供' if reference_image else '未提供'}")
        
        success, result = await self.api_client.generate_image(
            prompt=prompt,
            reference_image=reference_image,
            resolution=resolution
        )
        
        if not success:
            logger.error(f"生成图像失败: {result}")
            return f"❌ 生成自拍失败：{result}"
        
        logger.info(f"生成图像成功: {result}")
        # 这里需要根据API返回的实际格式进行处理
        # 假设返回的是图像URL
        # 注意：实际实现时需要根据豆包API的返回格式进行调整
        return result

    @filter.command("/自拍", alias={"自拍", "selfie"})
    async def selfie_command(self, event: AstrMessageEvent):
        """生成Bot自拍"""
        # 检查限流
        if self.cfg.rate_limit_enabled:
            user_id = self._get_user_id(event)
            allowed, message = self.rate_limiter.check_and_consume(user_id)
            if not allowed:
                yield event.plain_result(message)
                return
        
        # 显示生成中消息
        yield event.plain_result("🤳 正在生成自拍...")
        
        # 获取今日穿搭
        outfit = None
        if self.cfg.enable_auto_outfit:
            outfit = await self._get_today_outfit(event)
        
        if not outfit:
            outfit = "休闲装"
            logger.warning("未获取到今日穿搭，使用默认值")
        
        # 生成自拍
        result = await self._generate_selfie(event, outfit)
        
        # 返回结果
        if result.startswith("❌"):
            yield event.plain_result(result)
        else:
            yield event.image_result(result)
