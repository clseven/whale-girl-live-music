"""The stop route is local-only and stops new work before replying. No processes."""
import ast
import io
import json
import threading
import unittest
from email.message import Message
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

class LifecycleTests(unittest.TestCase):
    def handler(self, origin='http://127.0.0.1:18763', host='127.0.0.1:18763', content_type='application/json'):
        tree=ast.parse((Path(__file__).parents[1]/'server.py').read_text(encoding='utf-8'))
        handler=next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name=='Handler')
        worker=SimpleNamespace(active=True,epoch=7,pending_action=('next',7),lock=threading.RLock(),stop_event=threading.Event())
        studio=SimpleNamespace(stop_event=threading.Event())
        factory=Mock()
        from urllib.parse import urlparse
        scope={'SimpleHTTPRequestHandler':SimpleHTTPRequestHandler, 'urlparse':urlparse,
               'json':json,'threading':SimpleNamespace(Thread=factory),'STUDIO':studio,'WATCH':worker}
        exec(compile(ast.Module(body=[handler], type_ignores=[]),'<handler>','exec'),scope)
        instance=object.__new__(scope['Handler'])
        instance.path='/api/studio/lifecycle/stop'
        instance.headers=Message()
        for key,value in [('Host',host),('Origin',origin),('Content-Type',content_type),('Content-Length','2')]:
            instance.headers[key]=value
        instance.rfile=io.BytesIO(b'{}')
        instance.reply=Mock()
        instance.server=SimpleNamespace(shutdown=Mock())
        return instance,worker,studio,factory

    def test_local_stop_cancels_work_then_schedules_shutdown_without_clearing_data(self):
        handler,watch,studio,factory=self.handler()
        handler.do_POST()
        self.assertTrue(studio.stop_event.is_set())
        self.assertEqual(handler.reply.call_args.args[0],200)
        self.assertEqual(factory.call_args.kwargs['target'],handler.server.shutdown)
        factory.return_value.start.assert_called_once()

    def test_cross_site_cannot_stop_backend(self):
        handler,watch,studio,factory=self.handler(origin='https://example.com')
        handler.do_POST()
        self.assertEqual(handler.reply.call_args.args[0],403)
        self.assertFalse(studio.stop_event.is_set())
        self.assertTrue(watch.active)
        factory.assert_not_called()

    def test_unapproved_host_cannot_stop_backend(self):
        handler,watch,studio,factory=self.handler(host='evil.example:18763')
        handler.do_POST()
        self.assertEqual(handler.reply.call_args.args[0],403)
        factory.assert_not_called()

    def test_non_json_cannot_stop_backend(self):
        handler,watch,studio,factory=self.handler(content_type='text/plain')
        handler.do_POST()
        self.assertEqual(handler.reply.call_args.args[0],415)
        factory.assert_not_called()

if __name__=='__main__': unittest.main()
