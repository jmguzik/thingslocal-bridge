from types import SimpleNamespace
import unittest
from unittest.mock import patch
from thingslocal.network import APSession


class NetworkGuardTests(unittest.TestCase):
    def test_externally_managed_connection_is_rejected_before_profile_changes(self):
        session=APSession({'ssid':'Fixture_AP','bssid':'00:11:22:33:44:55'})
        with patch('thingslocal.network.nm',side_effect=[
                SimpleNamespace(stdout='wlan0:wifi:connected (externally)\n'),SimpleNamespace(stdout='--\n')]), \
                patch('thingslocal.network.command') as command:
            with self.assertRaises(ValueError):session.__enter__()
            command.assert_not_called()
            self.assertIsNone(session.temporary)

    def test_failed_restoration_keeps_independent_guard_active(self):
        session=APSession({'ssid':'Fixture_AP'});session.interface='wlan0';session.original='original'
        session.temporary='temporary';session.guard='thingslocal-fixture-restore'
        with patch('thingslocal.network.restore',return_value=False),patch('thingslocal.network.command') as command:
            session.__exit__(None,None,None)
            self.assertFalse(session.restored);command.assert_not_called()

    def test_successful_restoration_stops_only_its_own_guard(self):
        session=APSession({'ssid':'Fixture_AP'});session.interface='wlan0';session.original='original'
        session.temporary='temporary';session.guard='thingslocal-fixture-restore'
        with patch('thingslocal.network.restore',return_value=True),patch('thingslocal.network.command') as command:
            session.__exit__(None,None,None)
            self.assertTrue(session.restored)
            self.assertEqual(command.call_args.args,('systemctl','--user','stop',
                'thingslocal-fixture-restore.timer','thingslocal-fixture-restore.service'))

    def test_no_default_route_uses_actual_dhcp_router_not_a_fixed_dot_one(self):
        from thingslocal.network import ap_gateway
        with patch('thingslocal.network.nm',side_effect=[SimpleNamespace(stdout=''),
                SimpleNamespace(stdout='dhcp_server_identifier = 192.168.90.9\nrouters = 192.168.90.9\n'),
                SimpleNamespace(stdout='192.168.90.2/24\n')]):
            self.assertEqual(ap_gateway('wlan0'),'192.168.90.9')

    def test_cross_subnet_router_is_not_used_as_an_ap_gateway(self):
        from thingslocal.network import ap_gateway
        with patch('thingslocal.network.nm',side_effect=[SimpleNamespace(stdout=''),
                SimpleNamespace(stdout='routers = 192.168.30.10\n'),SimpleNamespace(stdout='192.168.90.2/24\n')]):
            with self.assertRaises(ValueError):ap_gateway('wlan0')

    def test_nmcli_pipe_separated_dhcp_options_from_real_ap(self):
        from thingslocal.network import ap_gateway
        with patch('thingslocal.network.nm',side_effect=[SimpleNamespace(stdout='\n'),
                SimpleNamespace(stdout='dhcp_server_identifier = 192.168.90.9 | requested_routers = 1 | routers = 192.168.90.9 | subnet_mask = 255.255.255.0\n'),
                SimpleNamespace(stdout='192.168.90.2/24\n')]):
            self.assertEqual(ap_gateway('wlan0'),'192.168.90.9')

    def test_own_address_is_not_a_router(self):
        from thingslocal.network import ap_gateway
        with patch('thingslocal.network.nm',side_effect=[SimpleNamespace(stdout='192.168.90.2\n'),
                SimpleNamespace(stdout='192.168.90.2/24\n')]):
            with self.assertRaises(ValueError):ap_gateway('wlan0')
