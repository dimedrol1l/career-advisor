import os
import unittest
from unittest.mock import patch

from app_config import ConfigurationError, validate_settings


class ConfigurationTests(unittest.TestCase):
    def test_cyrillic_placeholder_rejected_without_disclosing_key(self):
        secret = 'ваш_ключ_авторизации'
        with patch.dict(os.environ, {'GIGACHAT_CREDENTIALS': secret}):
            with self.assertRaises(ConfigurationError) as raised:
                validate_settings()
        self.assertNotIn(secret, str(raised.exception))
        self.assertIn('GIGACHAT_CREDENTIALS', str(raised.exception))

    def test_valid_ascii_credentials_and_scope(self):
        with patch.dict(os.environ, {'GIGACHAT_CREDENTIALS': 'dGVzdDp0ZXN0',
                                     'GIGACHAT_SCOPE': 'GIGACHAT_API_PERS'}):
            self.assertEqual(validate_settings(), 'dGVzdDp0ZXN0')

    def test_newline_in_key_is_rejected(self):
        with patch.dict(os.environ, {'GIGACHAT_CREDENTIALS': 'abc\ndef'}):
            with self.assertRaises(ConfigurationError):
                validate_settings()

    def test_invalid_scope_is_rejected(self):
        with patch.dict(os.environ, {'GIGACHAT_CREDENTIALS': 'abc', 'GIGACHAT_SCOPE': 'личный'}):
            with self.assertRaises(ConfigurationError):
                validate_settings()

    def test_invalid_output_token_budget_is_rejected(self):
        with patch.dict(os.environ, {'GIGACHAT_CREDENTIALS': 'abc', 'GIGACHAT_MAX_TOKENS': '0'}):
            with self.assertRaises(ConfigurationError):
                validate_settings()



if __name__ == '__main__':
    unittest.main()
