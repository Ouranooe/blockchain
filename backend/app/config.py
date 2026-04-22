import logging
import os

_DEFAULT_SECRET = "medshare-dev-secret-key"

logger = logging.getLogger(__name__)


class Settings:
    APP_NAME = os.getenv("APP_NAME", "MedShare Backend")
    API_PREFIX = "/api"
    SECRET_KEY = os.getenv("SECRET_KEY", _DEFAULT_SECRET)
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "600"))
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://medshare:medshare123@mysql:3306/medshare?charset=utf8mb4",
    )
    GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:3000/api")
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()

    # 迭代 4：文件加密与本地存储
    FILE_KEY_BASE64 = os.getenv("MEDSHARE_FILE_KEY_BASE64")  # base64(32 bytes)
    STORAGE_DIR = os.getenv(
        "MEDSHARE_STORAGE_DIR",
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "storage")),
    )
    MAX_FILE_SIZE_BYTES = int(os.getenv("MEDSHARE_MAX_FILE_SIZE", str(10 * 1024 * 1024)))
    ALLOWED_MIME_TYPES = frozenset(
        (
            os.getenv(
                "MEDSHARE_ALLOWED_MIME",
                "application/pdf,image/jpeg,image/png",
            )
        ).split(",")
    )

    @classmethod
    def check(cls) -> None:
        if cls.SECRET_KEY == _DEFAULT_SECRET and cls.ENVIRONMENT != "development":
            raise RuntimeError(
                "SECRET_KEY 未配置：生产环境必须通过环境变量设置 SECRET_KEY"
            )
        if cls.SECRET_KEY == _DEFAULT_SECRET:
            logger.warning("使用默认 SECRET_KEY，仅限开发环境")


settings = Settings()
settings.check()
