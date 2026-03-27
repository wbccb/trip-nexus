import bcrypt


def hash_password(password: str) -> str:
    raw_password = str(password or "")
    if len(raw_password) < 8:
        raise ValueError("密码长度不能少于 8 位")
    hashed = bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            str(password or "").encode("utf-8"),
            str(password_hash or "").encode("utf-8"),
        )
    except Exception:
        return False
