# python
import pytest
from types import SimpleNamespace

# Import the function to test (relative import as required)
from .auth import register
from . import auth as auth_module

# Fake DB result used to simulate db.execute(...).scalar_one_or_none()
class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

# Minimal fake AsyncSession-like object
class FakeDB:
    def __init__(self, scalar_res=None):
        # value returned by execute(...).scalar_one_or_none()
        self._scalar_res = scalar_res
        self.added = None

    async def execute(self, *args, **kwargs):
        return FakeResult(self._scalar_res)

    def add(self, obj):
        # store reference for assertions if needed
        self.added = obj

    async def commit(self):
        return None

    async def refresh(self, obj):
        # emulate DB assigning an id
        setattr(obj, "id", 1)

    async def get(self, model, id_):
        return None

@pytest.mark.asyncio
async def test_register_success_creator(monkeypatch):
    # get_user_by_email returns None => no existing email
    monkeypatch.setattr(auth_module, "get_user_by_email", lambda db, role, email: None)

    # Ensure duplicate username check returns None
    fake_db = FakeDB(scalar_res=None)

    # Patch hash_password to a deterministic value
    monkeypatch.setattr(auth_module, "hash_password", lambda pwd: "hashed_pwd")

    # Build payload-like object expected by register
    payload = SimpleNamespace(role="creator", username="testuser", email="test@example.com", password="secret")

    resp = await register(payload, db=fake_db)
    assert isinstance(resp, dict)
    assert resp.get("msg") == "User registered successfully"
    assert resp.get("user_id") == "1"

@pytest.mark.asyncio
async def test_register_duplicate_email(monkeypatch):
    # Simulate existing user for email check
    monkeypatch.setattr(auth_module, "get_user_by_email", lambda db, role, email: object())

    fake_db = FakeDB(scalar_res=None)
    payload = SimpleNamespace(role="brand", username="other", email="exists@example.com", password="pw")

    with pytest.raises(Exception) as excinfo:
        await register(payload, db=fake_db)
    # FastAPI raises HTTPException; ensure it's a 400 for "User already exists"
    assert hasattr(excinfo.value, "status_code")
    assert excinfo.value.status_code == 400
    assert "User already exists" in str(excinfo.value.detail)

@pytest.mark.asyncio
async def test_register_duplicate_username(monkeypatch):
    # No existing email
    monkeypatch.setattr(auth_module, "get_user_by_email", lambda db, role, email: None)

    # Simulate duplicate username via execute().scalar_one_or_none() returning truthy
    fake_db = FakeDB(scalar_res=object())

    payload = SimpleNamespace(role="admin", username="duplicate", email="unique@example.com", password="pw")
    with pytest.raises(Exception) as excinfo:
        await register(payload, db=fake_db)

    assert hasattr(excinfo.value, "status_code")
    assert excinfo.value.status_code == 400
    assert "Username already exists" in str(excinfo.value.detail)

@pytest.mark.asyncio
async def test_register_long_password_raises_value_error(monkeypatch):
    # No existing email and no duplicate username
    monkeypatch.setattr(auth_module, "get_user_by_email", lambda db, role, email: None)
    fake_db = FakeDB(scalar_res=None)

    # Simulate hash_password raising the bcrypt/ValueError reported
    def raise_long_pw(pw):
        raise ValueError("password cannot be longer than 72 bytes, truncate manually if necessary")
    monkeypatch.setattr(auth_module, "hash_password", raise_long_pw)

    payload = SimpleNamespace(role="brand", username="longpw", email="long@example.com", password="x" * 1000)

    with pytest.raises(ValueError) as excinfo:
        await register(payload, db=fake_db)
    assert "password cannot be longer than 72 bytes" in str(excinfo.value)