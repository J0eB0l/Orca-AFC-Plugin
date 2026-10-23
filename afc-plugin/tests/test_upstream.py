import io
import unittest
import urllib.error

import fake_orca  # noqa: F401  (puts afc-plugin/ on sys.path)
from orca_afc import upstream


def opener(sources):
    """urlopen for raw GitHub files: each branch's AFC.py head, or an error."""
    asked = []

    def urlopen(request, timeout, context):
        asked.append((request.full_url, request.get_header("Range")))
        branch = request.full_url.split("/")[-3]
        source = sources.get(branch)
        if source is None:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        return io.BytesIO(source.encode())

    urlopen.asked = asked
    return urlopen


class ReadTest(unittest.TestCase):
    def test_each_branchs_afc_version(self):
        urlopen = opener({"main": '# AFC\nAFC_VERSION="1.2.0"\n', "DEV": "AFC_VERSION = '1.2.8'\n"})
        self.assertEqual(upstream.read(urlopen), {"main": "1.2.0", "DEV": "1.2.8"})
        self.assertEqual(urlopen.asked[0], ("https://raw.githubusercontent.com/ArmoredTurtle/AFC-Klipper-Add-On/main/"
                                            "extras/AFC.py", "bytes=0-16383"))

    def test_a_branch_that_does_not_answer_is_left_out(self):
        self.assertEqual(upstream.read(opener({"DEV": 'AFC_VERSION="1.2.8"\n'})), {"DEV": "1.2.8"})

    def test_no_version_found(self):
        self.assertEqual(upstream.read(opener({"main": "nothing here", "DEV": ""})), {})


class NewerTest(unittest.TestCase):
    def test_a_branch_ahead_of_the_installed_version(self):
        self.assertTrue(upstream.newer("1.2.7", {"main": "1.2.0", "DEV": "1.2.8"}))
        self.assertTrue(upstream.newer("1.2.7", {"main": "1.10.0"}))

    def test_up_to_date_or_unknown(self):
        self.assertFalse(upstream.newer("1.2.8", {"main": "1.2.0", "DEV": "1.2.8"}))
        self.assertFalse(upstream.newer("", {"DEV": "1.2.8"}))
        self.assertFalse(upstream.newer("1.2.7", {}))


if __name__ == "__main__":
    unittest.main()
