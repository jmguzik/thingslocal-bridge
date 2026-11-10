import socket
import unittest
from unittest.mock import patch
from fixtures import configuration
from thingslocal.security import verify_route


class RoutingTests(unittest.TestCase):
    def test_public_or_additional_ipv6_record_is_rejected_before_connection(self):
        config=configuration();ip=config['broker']['lan_ip'];port=8883
        addresses=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,port)),
                   (socket.AF_INET6,socket.SOCK_STREAM,6,'',('2001:db8::123',port,0,0))]
        with patch('thingslocal.security.socket.getaddrinfo',return_value=addresses), \
                patch('thingslocal.security.socket.create_connection') as connect:
            with self.assertRaises(ValueError):verify_route(config)
            connect.assert_not_called()

    def test_only_selected_private_ipv4_is_accepted(self):
        config=configuration();ip=config['broker']['lan_ip']
        with patch('thingslocal.security.socket.getaddrinfo',return_value=[
                (socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,8883))]), \
                patch('thingslocal.security.socket.create_connection'):
            self.assertEqual(verify_route(config),'ssl://bridge.example.test:8883')
