import pytest
from fastapi.testclient import TestClient
from asyncpg.exceptions import (
    UniqueViolationError,
    ForeignKeyViolationError,
    CheckViolationError,
    NotNullViolationError,
    DataError,
    InvalidTextRepresentationError,
)
from backend.api.app import app

# Register dummy endpoints to trigger the exception handlers
@app.get("/test-db-error/unique")
async def trigger_unique():
    raise UniqueViolationError("unique constraint violated")

@app.get("/test-db-error/fk")
async def trigger_fk():
    raise ForeignKeyViolationError("foreign key constraint violated")

@app.get("/test-db-error/check")
async def trigger_check():
    raise CheckViolationError("check constraint violated")

@app.get("/test-db-error/not-null")
async def trigger_not_null():
    raise NotNullViolationError("not null constraint violated")

@app.get("/test-db-error/data")
async def trigger_data():
    raise DataError("data error")

@app.get("/test-db-error/invalid-text")
async def trigger_invalid_text():
    raise InvalidTextRepresentationError("invalid text representation")

def test_exception_translation_unique():
    client = TestClient(app)
    response = client.get("/test-db-error/unique")
    assert response.status_code == 409
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "DUPLICATE_RESOURCE"
    assert data["error"]["message"] == "A resource with the specified unique attributes already exists."

def test_exception_translation_fk():
    client = TestClient(app)
    response = client.get("/test-db-error/fk")
    assert response.status_code == 400
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "INVALID_REFERENCE"
    assert data["error"]["message"] == "The request references a linked resource that does not exist or is in use."

def test_exception_translation_check():
    client = TestClient(app)
    response = client.get("/test-db-error/check")
    assert response.status_code == 400
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "CONSTRAINT_VIOLATION"
    assert data["error"]["message"] == "The provided parameters violate business validation rules."

def test_exception_translation_not_null():
    client = TestClient(app)
    response = client.get("/test-db-error/not-null")
    assert response.status_code == 400
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "MISSING_FIELD"
    assert data["error"]["message"] == "A required field was not provided."

def test_exception_translation_data():
    client = TestClient(app)
    response = client.get("/test-db-error/data")
    assert response.status_code == 422
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "TYPE_MISMATCH"
    assert data["error"]["message"] == "Invalid data format or type mismatch detected."

def test_exception_translation_invalid_text():
    client = TestClient(app)
    response = client.get("/test-db-error/invalid-text")
    assert response.status_code == 422
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "TYPE_MISMATCH"
    assert data["error"]["message"] == "Invalid data format or type mismatch detected."
