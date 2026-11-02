import importlib
import json
from pathlib import Path
import tempfile
import unittest


class FaultMailboxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.module=importlib.import_module('scripts.run_p0_fault_test')

    def test_action_is_executed_once_and_ack_is_correlated(self):
        mailbox=self.module.FaultMailbox(self.root)
        calls=[]
        self.assertIsNone(mailbox.poll(lambda action:calls.append(action)))
        (self.root/'fault-request.json').write_text(json.dumps({'request_id':1,'action':'start_worker'}))
        mailbox.poll(lambda action:(calls.append(action) or {'session_id':'new'}))
        mailbox.poll(lambda action:calls.append(action))
        self.assertEqual(calls,['start_worker'])
        self.assertEqual(json.loads((self.root/'fault-response.json').read_text()),
                         {'request_id':1,'ok':True,'session_id':'new'})

    def test_invalid_or_reused_request_cannot_trigger_process_actions(self):
        mailbox=self.module.FaultMailbox(self.root)
        calls=[]
        for request in [{'request_id':True,'action':'crash_worker'},
                        {'request_id':1,'action':'kill','pid':1},
                        {'request_id':1,'action':'crash_worker','pid':1}]:
            (self.root/'fault-request.json').write_text(json.dumps(request))
            with self.assertRaises(self.module.SupervisorError):mailbox.poll(calls.append)
        self.assertEqual(calls,[])

    def test_action_failure_keeps_a_failed_correlated_ack(self):
        mailbox=self.module.FaultMailbox(self.root)
        (self.root/'fault-request.json').write_text(json.dumps({'request_id':3,'action':'reload_worker'}))
        def fail(_):raise RuntimeError('startup failed')
        with self.assertRaises(RuntimeError):mailbox.poll(fail)
        self.assertEqual(json.loads((self.root/'fault-response.json').read_text()),
                         {'request_id':3,'ok':False,'error':'startup failed'})
