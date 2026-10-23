import unittest

from examples.ch02_model_protocol import run_case, validate_add


class ChapterTwoProtocolTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(run_case({"a": 2, "b": 3}), ("5", "done"))

    def test_missing_parameter(self):
        observation, text = run_case({"a": 2})
        self.assertIn("Tool error: TypeError", observation)
        self.assertEqual(text, "done")

    def test_extra_field(self):
        with self.assertRaisesRegex(ValueError, "exactly"):
            validate_add({"a": 1, "b": 2, "secret": "x"})

    def test_bool_is_not_integer(self):
        with self.assertRaisesRegex(TypeError, "integers"):
            validate_add({"a": True, "b": 2})


if __name__ == "__main__":
    unittest.main()
