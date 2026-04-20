import os
import tempfile
import unittest


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_PATH"] = os.path.join(self.tempdir.name, "test.db")

        import importlib
        import app.database

        importlib.reload(app.database)
        self.db = app.database
        self.db.init_db()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_create_confirm_unsubscribe_flow(self) -> None:
        token = "token-1"
        created = self.db.create_or_update_subscriber("user@example.com", token)
        self.assertEqual(created["status"], "created")

        confirmed = self.db.confirm_by_token(token)
        self.assertTrue(confirmed)

        subscribers = self.db.list_subscribers()
        self.assertEqual(len(subscribers), 1)
        self.assertEqual(subscribers[0]["confirmed"], 1)

        unsubscribed = self.db.unsubscribe_by_token(token)
        self.assertTrue(unsubscribed)

        subscribers_after = self.db.list_subscribers()
        self.assertEqual(subscribers_after[0]["confirmed"], 0)


if __name__ == "__main__":
    unittest.main()
