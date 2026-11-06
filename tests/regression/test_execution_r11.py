"""Observed provider trailer and ledger contention: offline execution regressions."""
import copy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests.common.execution.responses import terminal_analysis, delivery_payload, ResponseProtocolError
from tests.common.execution.budget import Ledger, BudgetDenied
from tests.common.execution.progress import ProgressBudget
from tests.regression import test_gateway_retries as fixtures


def trailer():
    return {'id':'', 'model':'', 'created_at':0, 'status':'failed', 'output':None, 'usage':None,
            'error':{'message':'responses stream ended without response.completed: scanner_error',
                     'type':'up_stream_break', 'code':'up_stream_break', 'param':None}}


def frame(value, newline='\n'):
    kind='response.'+value['status']
    return (f'event: {kind}{newline}data: '+json.dumps({'type':kind,'response':value})+newline*2).encode()


class TerminalPolicy(unittest.TestCase):
    def test_gateway_keeps_original_bytes_and_delivers_only_completed_frame_once(self):
        good=json.loads(fixtures.response().getvalue().decode().split('data: ')[1])['response']
        for newline in ('\n','\r\n'):
            with self.subTest(newline=newline):
                raw=frame(good,newline)+frame(trailer(),newline)
                result=fixtures.GatewayRetries().exercise([lambda:io.BytesIO(raw)])
                self.assertEqual(result['statuses'],[200])
                self.assertEqual(len(result['calls']),1)
                self.assertEqual(result['receipts'][0]['payload'].encode(),raw)
                self.assertEqual(result['delivered'],frame(good,newline))
                self.assertEqual(len(result['ledger']['requests']),1)
                self.assertEqual(result['ledger']['requests'][0]['status'],'usage_recorded')
                events=[e for e in result['events'] if e['event']=='response_normalized']
                self.assertEqual(events[0]['ignored_scanner_trailers'],1)

    def test_real_conflicts_and_unproven_success_are_rejected(self):
        good=json.loads(fixtures.response().getvalue().decode().split('data: ')[1])['response']
        for key,value in [('id','other'),('model','other'),('output',[{}]),('usage',{})]:
            changed={**good,key:value}
            with self.subTest(key=key), self.assertRaises(ResponseProtocolError):
                terminal_analysis(frame(good)+frame(changed))
        for key,value in [('id','real-failed'),('model','real-model'),('output',[{}]),('usage',{}),('created_at',1)]:
            with self.subTest(trailer_key=key), self.assertRaises(ResponseProtocolError):
                terminal_analysis(frame(good)+frame({**trailer(),key:value}))
        bad=copy.deepcopy(trailer());bad['error']['message']='other error'
        for payload in (frame(trailer())+frame(good), frame(good)+frame(bad),
                        frame(good)+frame(trailer())+frame(good),
                        frame({**good,'usage':None})+frame(trailer()),
                        frame(good)+b'data: {"type":"response.output_text.delta"}\n\n'):
            with self.assertRaises(ResponseProtocolError):terminal_analysis(payload)

    def test_identical_terminal_dedup_removes_event_header_too(self):
        good=json.loads(fixtures.response().getvalue().decode().split('data: ')[1])['response']
        raw=frame(good)
        self.assertEqual(delivery_payload(raw+raw),raw)
        self.assertEqual(terminal_analysis(raw+raw)[1]['duplicate_terminals'],1)


class LedgerContention(unittest.TestCase):
    def test_wal_reader_during_write_and_atomic_concurrent_reservations(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger=Ledger(Path(folder)/'budget.sqlite',1)
            with ledger.connect() as writer:
                self.assertEqual(writer.execute('PRAGMA journal_mode').fetchone()[0],'wal')
                self.assertEqual(writer.execute('PRAGMA synchronous').fetchone()[0],2)
                writer.execute('BEGIN IMMEDIATE')
                writer.execute("INSERT INTO requests VALUES ('pending','x',1,NULL,'reserved',1,NULL)")
                with ThreadPoolExecutor(1) as pool:
                    result=pool.submit(ledger.summary).result(timeout=2)
                self.assertEqual(result['requests'],[])
            def reserve(n):
                try:return ledger.reserve(str(n),100_000,20,64)
                except BudgetDenied:return None
            with ThreadPoolExecutor(4) as pool:
                ids=[x for x in pool.map(reserve,range(20)) if x]
            self.assertEqual(len(ids),9)  # One micro-CNY already reserved.
            self.assertEqual(len(set(ids)),9)
            self.assertEqual(len(ledger.summary()['requests']),10)
            self.assertLessEqual(ledger.summary()['accounted_upper_cny'],1)

    def test_existing_delete_journal_is_not_migrated_on_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.sqlite'
            ledger=Ledger(path)
            with ledger.connect() as db:db.execute('PRAGMA journal_mode=DELETE')
            reopened=Ledger.for_run(path)
            with reopened.connect() as db:
                self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0],'delete')

    def test_busy_progress_is_explicit_and_recovers_but_corruption_is_not_swallowed(self):
        fresh={'requests':[], 'accounted_upper_cny':0}
        reader=ProgressBudget(SimpleNamespace(summary=Mock(side_effect=[
            sqlite3.OperationalError('database is locked'),fresh,
            sqlite3.OperationalError('database is locked'),fresh,
            sqlite3.OperationalError('no such table: requests')])))
        self.assertEqual(reader.read()[1]['status'],'unavailable')
        self.assertEqual(reader.read()[1]['status'],'fresh')
        stale,meta=reader.read()
        self.assertIs(stale,fresh);self.assertEqual(meta['status'],'stale')
        self.assertEqual(reader.read()[1]['status'],'fresh')
        with self.assertRaises(sqlite3.OperationalError):reader.read()


if __name__=='__main__':unittest.main()
