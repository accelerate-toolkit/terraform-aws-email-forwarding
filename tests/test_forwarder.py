# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

spec = importlib.util.spec_from_file_location("forwarder", Path(__file__).parents[1] / "lambda/forwarder.py")
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)

class ConditionalFailure(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}

class ForwarderTests(unittest.TestCase):
    def message(self):
        message = EmailMessage()
        message["From"] = "Original <original@example.net>"
        message["To"] = "recipient@example.org"
        message["Subject"] = "Research data"
        message["DKIM-Signature"] = "invalid-after-rewrite"
        message["Authentication-Results"] = "untrusted"
        message["Bcc"] = "private@example.net"
        message.set_content("plain body")
        message.add_alternative('<p>HTML body<img src="cid:picture"></p>', subtype="html")
        message.get_payload()[1].add_related(b"image-bytes", maintype="image", subtype="png", cid="<picture>")
        message.add_attachment(b"binary\x00payload", maintype="application", subtype="octet-stream", filename="data.bin")
        return message

    def test_preserves_body_attachments_and_inline_images_but_replaces_transport_headers(self):
        output = f.compose(self.message().as_bytes(), "forward@example.org", "inbox@example.net", "signal", 100000)
        parsed = BytesParser(policy=policy.default).parsebytes(output)
        self.assertEqual(parsed["From"], "forward@example.org")
        self.assertEqual(parsed["Reply-To"], "original@example.net")
        self.assertIsNone(parsed["DKIM-Signature"])
        self.assertIsNone(parsed["Authentication-Results"])
        self.assertIsNone(parsed["Bcc"])
        parts = list(parsed.walk())
        self.assertTrue(any(p.get_content_type() == "text/html" for p in parts))
        self.assertTrue(any(p.get("Content-ID") == "<picture>" and p.get_payload(decode=True) == b"image-bytes" for p in parts))
        self.assertTrue(any(p.get_filename() == "data.bin" and p.get_payload(decode=True) == b"binary\x00payload" for p in parts))

    def test_html_only_is_not_lost(self):
        m = EmailMessage(); m.set_content("<p>Only HTML</p>", subtype="html")
        parsed = BytesParser(policy=policy.default).parsebytes(f.compose(m.as_bytes(), "a@example.org", "b@example.net", "test", 10000))
        self.assertEqual(parsed.get_content_type(), "text/html")
        self.assertIn("Only HTML", parsed.get_content())

    def test_loop_and_size_rejected(self):
        m = self.message(); m["X-Email-Forwarded"] = "another-project"
        with self.assertRaisesRegex(ValueError, "loop"):
            f.compose(m.as_bytes(), "a@example.org", "b@example.net", "test", 10000)
        with self.assertRaisesRegex(ValueError, "limit"):
            f.compose(self.message().as_bytes(), "a@example.org", "b@example.net", "test", 10)

    def config(self):
        return dict(RECIPIENT="recipient@example.org", RAW_BUCKET="raw", MAX_MESSAGE_BYTES="100000", FORWARD_FROM="recipient@example.org", FORWARD_TO="inbox@example.net", NAME="signal", CONFIGURATION_SET="signal")

    def record(self):
        return {"ses": {"mail": {"messageId": "message-123"}, "receipt": {"recipients": ["recipient@example.org"], "spamVerdict": {"status": "PASS"}, "virusVerdict": {"status": "PASS"}}}}

    def test_success_records_ses_delivery_id(self):
        s3, ses, table = Mock(), Mock(), Mock()
        s3.get_object.return_value = {"Body": io.BytesIO(self.message().as_bytes())}
        ses.send_email.return_value = {"MessageId": "delivery-456"}
        self.assertEqual(f.process(self.record(), self.config(), s3, ses, table), "sent")
        self.assertEqual(table.update_item.call_args.kwargs["ExpressionAttributeValues"][":delivery"], "delivery-456")
        self.assertEqual(ses.send_email.call_args.kwargs["Destination"], {"ToAddresses": ["inbox@example.net"]})

    def test_successful_duplicate_does_not_read_or_send(self):
        s3, ses, table = Mock(), Mock(), Mock()
        table.put_item.side_effect = ConditionalFailure()
        table.get_item.return_value = {"Item": {"status": "sent"}}
        self.assertEqual(f.process(self.record(), self.config(), s3, ses, table), "duplicate")
        s3.get_object.assert_not_called(); ses.send_email.assert_not_called()

    def test_ambiguous_delivery_never_resends_automatically(self):
        s3, ses, table = Mock(), Mock(), Mock()
        table.put_item.side_effect = ConditionalFailure()
        table.get_item.return_value = {"Item": {"status": "processing"}}
        with self.assertRaisesRegex(RuntimeError, "review"):
            f.process(self.record(), self.config(), s3, ses, table)
        ses.send_email.assert_not_called()

    def test_send_failure_does_not_record_success_or_expose_sdk_error(self):
        s3, ses, table = Mock(), Mock(), Mock()
        s3.get_object.return_value = {"Body": io.BytesIO(self.message().as_bytes())}
        ses.send_email.side_effect = RuntimeError("secret recipient text")
        with self.assertRaisesRegex(RuntimeError, "Delivery incomplete") as error:
            f.process(self.record(), self.config(), s3, ses, table)
        self.assertNotIn("secret", str(error.exception))
        table.update_item.assert_not_called()

    def test_scan_failure_and_wrong_recipient_never_send(self):
        bad_scan = self.record(); bad_scan["ses"]["receipt"]["virusVerdict"]["status"] = "FAIL"
        bad_recipient = self.record(); bad_recipient["ses"]["receipt"]["recipients"] = ["other@example.org"]
        for record in [bad_scan, bad_recipient]:
            s3, ses, table = Mock(), Mock(), Mock()
            with self.assertRaises(ValueError):
                f.process(record, self.config(), s3, ses, table)
            table.put_item.assert_not_called(); ses.send_email.assert_not_called()

if __name__ == "__main__":
    unittest.main()
