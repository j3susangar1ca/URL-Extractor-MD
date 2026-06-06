import pytest
from pathlib import Path
from url_extractor_md.infrastructure.validation.input_validator import InputValidator
from url_extractor_md.domain.exceptions import ValidationError, SecurityError, StorageError

def test_validate_url_success():
    validator = InputValidator()
    # Should not raise any exception
    validator.validate_url("https://example.com/some/path")
    validator.validate_url("http://sub.domain.org:8080/test?query=1")

def test_validate_url_failures():
    validator = InputValidator()
    
    # Empty URL
    with pytest.raises(ValidationError) as exc:
        validator.validate_url("")
    assert "empty" in str(exc.value)

    # URL length limit
    with pytest.raises(ValidationError) as exc:
        validator.validate_url("http://" + "a" * 2050)
    assert "exceeds maximum length" in str(exc.value)

    # Forbidden schemes
    for scheme in ["file://host/path", "ftp://host", "javascript:alert(1)", "data:text/html,test"]:
        with pytest.raises(SecurityError) as exc:
            validator.validate_url(scheme)
        assert "not allowed" in str(exc.value)

    # Missing netloc
    with pytest.raises(ValidationError) as exc:
        validator.validate_url("http:///only/path")
    assert "network location" in str(exc.value)

def test_validate_filename_success():
    validator = InputValidator()
    assert validator.validate_filename("valid-file_name.123.md") == "valid-file_name.123.md"
    assert validator.validate_filename("simple name") == "simple name"

def test_validate_filename_failures():
    validator = InputValidator()

    # Empty filename
    with pytest.raises(ValidationError) as exc:
        validator.validate_filename("")
    assert "empty" in str(exc.value)

    # Path traversal detection (basename mismatch)
    with pytest.raises(SecurityError) as exc:
        validator.validate_filename("dir/file.txt")
    assert "Path traversal detected" in str(exc.value)

    with pytest.raises(SecurityError) as exc:
        validator.validate_filename("../file.txt")
    assert "Path traversal detected" in str(exc.value)

    # Illegal characters
    with pytest.raises(SecurityError) as exc:
        validator.validate_filename("file*.txt")
    assert "illegal characters" in str(exc.value)

def test_validate_directory_success(tmp_path):
    validator = InputValidator()
    target_dir = tmp_path / "subdir" / "another"
    res = validator.validate_directory(target_dir)
    assert res.is_dir()
    assert res == target_dir.resolve()
