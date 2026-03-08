import unittest

from gdrive_cli import __version__


class VersionTests(unittest.TestCase):
    def test_version_is_not_empty(self):
        self.assertTrue(__version__)
        self.assertNotEqual(__version__.strip(), "")
