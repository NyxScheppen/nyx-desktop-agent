"""Nyx 桌面陪伴 AI 后端包。"""
from dotenv import load_dotenv

# 包导入即加载 .env：huggingface_hub 的 ENDPOINT 常量在 import 时固化，
# 须在其被任何子模块 import 之前设置 HF_ENDPOINT，否则国内直连 huggingface.co 超时。
load_dotenv()
