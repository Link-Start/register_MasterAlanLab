import re

import pytest

from models import RegistrationInput
from utils import random_account_name


@pytest.mark.parametrize("prefix", ["", "Alan_", "A" * 48])
def test_generated_names_are_readable_and_valid_with_optional_prefix(prefix):
    for _ in range(12):
        name = random_account_name(prefix)
        nickname = name[len(prefix) :]
        assert re.fullmatch(r"(?:[A-Z][a-z]+){2}[0-9]{4}", nickname)
        assert name.startswith(prefix)
        RegistrationInput("user@example.com", name, "Password123!")
