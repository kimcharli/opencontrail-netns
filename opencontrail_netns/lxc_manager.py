import re
import subprocess
import sys


def shell_command(str):
    str = "sudo %s" % (str)
    cmd = subprocess.check_output(str, shell=True)
    return cmd


class LxcManager(object):
    def __init__(self):
        pass

    def _interface_generate_unique_name(self):
        output = shell_command('ip link list')
        ids = {}

        for line in output.split('\n'):
            m = re.match(r'[\d]+: instance([\d]+)', line)
            if m:
                ids[m.group(1)] = True

        for i in range(256):
            if str(i) in ids:
                continue
            return 'instance%d' % i
        return None

    def create_interface(self, daemon, ifname_instance):
        ifname_master = self._interface_generate_unique_name()
        shell_command('ip link add %s type veth peer name %s' %
                      (ifname_instance, ifname_master))
        shell_command('ip link set %s netns ns-%s' % (ifname_instance, daemon))
        shell_command('ip link set %s up' % ifname_master)
        return ifname_master

    def _interface_list_contains(self, output, iface):
        for line in output.split('\n'):
            m = re.match(r'[\d]+: ' + iface + ':', line)
            if m:
                return True
        return False

    def _get_master_ifname(self, daemon, ifname_instance):
        output = shell_command('ip netns exec ns-%s ethtool -S %s' %
                               (daemon, ifname_instance))
        m = re.search(r'peer_ifindex: (\d+)', output)
        ifindex = m.group(1)
        output = shell_command('ip link list')
        expr = '^' + ifindex + ': (\w+): '
        regex = re.compile(expr, re.MULTILINE)
        m = regex.search(output)
        return m.group(1)

    def interface_update(self, daemon, vmi, ifname_instance):
        """
        1. Make sure that the interface exists in the name space.
        2. Update the mac address.
        """
        output = shell_command('ip netns exec ns-%s ip link list' % daemon)
        if not self._interface_list_contains(output, ifname_instance):
            ifname_master = self.create_interface(daemon, ifname_instance)
        else:
            ifname_master = self._get_master_ifname(daemon, ifname_instance)

        mac = vmi.virtual_machine_interface_mac_addresses.mac_address[0]
        shell_command('ip netns exec ns-%s ifconfig %s hw ether %s' %
                      (daemon, ifname_instance, mac))
        return ifname_master

    def interface_config(self, daemon, ifname_guest, advertise_default=True,
                         ip_prefix=None):
        """
        Once the interface is operational, configure the IP addresses.
        For a bi-directional interface we use dhclient.
        """
        if advertise_default:
            shell_command('ip netns exec ns-%s dhclient %s' %
                          (daemon, ifname_guest))
        else:
            shell_command('ip netns exec ns-%s ip addr replace %s/%d dev %s' %
                          (daemon, ip_prefix[0], ip_prefix[1], ifname_guest))
            shell_command('ip netns exec ns-%s ip link set %s up' %
                          (daemon, ifname_guest))
            # disable reverse path filtering
            shell_command('ip netns exec ns-%s sh -c '
                          '"echo 2 >/proc/sys/net/ipv4/conf/%s/rp_filter"' %
                          (daemon, ifname_guest))

    def clear_interfaces(self, daemon):
        output = shell_command('ip netns exec ns-%s ip link list' % daemon)
        for line in output.split('\n'):
            m = re.match(r'^[\d]+: ([\w]+):', line)
            if m:
                ifname = m.group(1)
                if ifname == 'lo':
                    continue
                shell_command('ip netns exec ns-%s ip link delete %s' %
                              (daemon, ifname))

    def namespace_init(self, daemon):
        output = shell_command('ip netns list')
        for line in output.split():
            if line == 'ns-' + daemon:
                return False
        shell_command('ip netns add ns-%s' % daemon)
        return True

    def namespace_delete(self, daemon):
        shell_command('ip netns delete ns-%s' % daemon)

    def set_nat(self, daemon, cidr, itf):
        shell_command('ip netns exec ns-%s sh -c '
                      '"echo 1 > /proc/sys/net/ipv4/ip_forward"' % daemon)
        shell_command('ip netns exec ns-%s iptables -t nat -A POSTROUTING '
                      '-s %s -o %s -j MASQUERADE' % (daemon, cidr, itf))

    def set_route_via_interface(self, daemon, subnet, interface):
        shell_command('ip netns exec ns-%s ip route replace %s '
                      'dev %s' % (daemon, subnet, interface))

    def set_default_route(self, daemon, nh, interface):
        shell_command('ip netns exec ns-%s ip route replace default via %s '
                      'dev %s' % (daemon, nh, interface))
