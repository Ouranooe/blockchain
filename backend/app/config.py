import os


class Settings:
    APP_NAME = os.getenv("APP_NAME", "MedShare Backend")
    API_PREFIX = "/api"
    SECRET_KEY = os.getenv("SECRET_KEY", "medshare-dev-secret-key")
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "600"))
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://medshare:medshare123@mysql:3306/medshare?charset=utf8mb4",
    )
    GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:3000/api")


settings = Settings()
