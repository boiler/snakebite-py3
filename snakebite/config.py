import os
import logging
from xml.etree import ElementTree, ElementInclude
from urllib.parse import urlparse

from snakebite.namenode import Namenode

log = logging.getLogger(__name__)


class HDFSConfig(object):
    @classmethod
    def get_config_from_env(cls):
        """
        .. deprecated:: 2.5.3
        Gets configuration out of environment.

        Returns list of dicts - list of namenode representations
        """
        core_path = os.path.join(os.environ['HADOOP_HOME'], 'conf', 'core-site.xml')
        core_configs = cls.read_core_config(core_path)

        hdfs_path = os.path.join(os.environ['HADOOP_HOME'], 'conf', 'hdfs-site.xml')
        hdfs_configs = cls.read_hdfs_config(hdfs_path)

        if (not core_configs) and (not hdfs_configs):
            raise Exception("No config found in %s nor in %s" % (core_path, hdfs_path))

        configs = {
            'use_trash': hdfs_configs.get('use_trash', core_configs.get('use_trash', False)),
            'use_sasl': core_configs.get('use_sasl', False),
            'hdfs_namenode_principal': hdfs_configs.get('hdfs_namenode_principal', None),
            'nameservices': hdfs_configs.get('nameservices', {}) or core_configs.get('nameservices', {}),
        }

        return configs

    @staticmethod
    def read_hadoop_config(hdfs_conf_path):
        if os.path.exists(hdfs_conf_path):
            try:
                tree = ElementTree.parse(hdfs_conf_path)
                root = tree.getroot()
                if root.findall("{http://www.w3.org/2001/XInclude}include"):
                    def xloader(href, parse='xml', encoding='utf-8'):
                        xpath = href if os.path.isabs(href) else os.path.join(os.path.dirname(hdfs_conf_path), href)
                        with open(xpath, 'rb') as f:
                            return ElementTree.parse(f).getroot()
                    ElementInclude.include(root, loader=xloader)
                    for c in root.findall("./configuration"):
                        for p in c.findall("./property"):
                            root.insert(0, p)
                        root.remove(c)
            except:
                raise
                log.error("Unable to parse %s" % hdfs_conf_path)
                return
            for p in root.findall("./property"):
                yield p

    @classmethod
    def read_core_config(cls, core_site_path):
        configs = {}

        nameservices = {}
        for property in cls.read_hadoop_config(core_site_path):

            # fs.default.name is the key name for the file system on EMR clusters
            if property.findall('name')[0].text in ('fs.defaultFS', 'fs.default.name'):
                parse_result = urlparse(property.findall('value')[0].text)
                ns = parse_result.netloc
                nameservices = { ns: {"namenodes": [ns]} }
                log.debug("Got namenode '%s' from %s" % (parse_result.geturl(), core_site_path))
                configs['default_scheme'] = parse_result.scheme
                configs['default_nameservice'] = parse_result.netloc

            if property.findall('name')[0].text == 'fs.trash.interval':
                configs['use_trash'] = True

            if property.findall('name')[0].text == 'hadoop.security.authentication':
                log.debug("Got hadoop.security.authentication '%s'" % (property.findall('value')[0].text))
                if property.findall('value')[0].text == 'kerberos':
                    configs['use_sasl'] = True
                else:
                    configs['use_sasl'] = False

        if nameservices:
            configs['nameservices'] = nameservices

        return configs

    @classmethod
    def read_hdfs_config(cls, hdfs_site_path):
        configs = {}
        nameservices_namenodes = {}
        nameservices_viewfs_links = {}

        for property in cls.read_hadoop_config(hdfs_site_path):
            if property.findall('name')[0].text.startswith("dfs.namenode.rpc-address"):
                nameservice = property.findall('name')[0].text.split('.')[3]
                nameservices_namenodes.setdefault(nameservice, [])
                nameservices_namenodes[nameservice].append(property.findall('value')[0].text)
                #log.debug("Got namenode '%s' from %s" % (parse_result.geturl(), hdfs_site_path))

            if property.findall('name')[0].text.startswith("fs.viewfs.mounttable."):
                p = property.findall('name')[0].text.split('.')[4]
                if p in ('link', 'linkFallback'):
                    nameservice = property.findall('name')[0].text.split('.')[3]
                    nameservices_viewfs_links.setdefault(nameservice, {})
                    parse_result = urlparse(property.findall('value')[0].text)
                    ns = parse_result.netloc
                    path = parse_result.path if parse_result.path else '/'
                    src = '_fallback' if p == 'linkFallback' else property.findall('name')[0].text.split('.',5)[5]
                    nameservices_viewfs_links[nameservice][src] = (ns, path)

            if property.findall('name')[0].text == 'fs.trash.interval':
                configs['use_trash'] = True

            if property.findall('name')[0].text == 'dfs.namenode.kerberos.principal':
                log.debug("hdfs principal found: '%s'" % (property.findall('value')[0].text))
                configs['hdfs_namenode_principal'] = property.findall('value')[0].text

            if property.findall('name')[0].text == 'dfs.client.retry.max.attempts':
                configs['client_retries'] = int(property.findall('value')[0].text)

            if property.findall('name')[0].text == 'dfs.client.socket-timeout':
                configs['socket_timeout_millis'] = int(property.findall('value')[0].text)

            if property.findall('name')[0].text == 'dfs.client.failover.sleep.base.millis':
                configs['client_sleep_base_millis'] = int(property.findall('value')[0].text)

            if property.findall('name')[0].text == 'dfs.client.failover.sleep.max.millis':
                configs['client_sleep_max_millis'] = int(property.findall('value')[0].text)

            if property.findall('name')[0].text == 'dfs.client.failover.max.attempts':
                configs['failover_max_attempts'] = int(property.findall('value')[0].text)

            if property.findall('name')[0].text == 'dfs.client.use.datanode.hostname':
                configs['use_datanode_hostname'] = bool(property.findall('value')[0].text)

        if nameservices_namenodes:
            configs['nameservices'] = {}
            for ns, nns in nameservices_namenodes.items():
                configs['nameservices'].setdefault(ns, {})
                configs['nameservices'][ns]['namenodes'] = nns

        for ns, links in nameservices_viewfs_links.items():
            if not ns in configs['nameservices']:
                continue
            configs['nameservices'][ns]['links'] = links

        return configs

    core_try_paths = ('/etc/hadoop/conf/core-site.xml',
                      '/usr/local/etc/hadoop/conf/core-site.xml',
                      '/usr/local/hadoop/conf/core-site.xml')

    hdfs_try_paths = ('/etc/hadoop/conf/hdfs-site.xml',
                      '/usr/local/etc/hadoop/conf/hdfs-site.xml',
                      '/usr/local/hadoop/conf/hdfs-site.xml')

    @classmethod
    def get_external_config(cls):
        if os.environ.get('SNAKEBITE_HADOOP_CONF_DIR'):
            hdfs_path = os.path.join(os.environ['SNAKEBITE_HADOOP_CONF_DIR'], 'hdfs-site.xml')
            cls.hdfs_try_paths = (hdfs_path,) + cls.hdfs_try_paths
            core_path = os.path.join(os.environ['SNAKEBITE_HADOOP_CONF_DIR'], 'core-site.xml')
            cls.core_try_paths = (core_path,) + cls.core_try_paths
        elif os.environ.get('HADOOP_CONF_DIR'):
            hdfs_path = os.path.join(os.environ['HADOOP_CONF_DIR'], 'hdfs-site.xml')
            cls.hdfs_try_paths = (hdfs_path,) + cls.hdfs_try_paths
            core_path = os.path.join(os.environ['HADOOP_CONF_DIR'], 'core-site.xml')
            cls.core_try_paths = (core_path,) + cls.core_try_paths
        elif os.environ.get('HADOOP_HOME'):
            hdfs_path = os.path.join(os.environ['HADOOP_HOME'], 'conf', 'hdfs-site.xml')
            cls.hdfs_try_paths = (hdfs_path,) + cls.hdfs_try_paths
            core_path = os.path.join(os.environ['HADOOP_HOME'], 'conf', 'core-site.xml')
            cls.core_try_paths = (core_path,) + cls.core_try_paths

        # Try to find other paths
        core_configs = {}
        for core_conf_path in cls.core_try_paths:
            core_configs = cls.read_core_config(core_conf_path)
            if core_configs:
                break

        hdfs_configs = {}
        for hdfs_conf_path in cls.hdfs_try_paths:
            hdfs_configs = cls.read_hdfs_config(hdfs_conf_path)
            if hdfs_configs:
                break

        configs = {
            'use_trash': hdfs_configs.get('use_trash', core_configs.get('use_trash', False)),
            'use_sasl': core_configs.get('use_sasl', False),
            'hdfs_namenode_principal': hdfs_configs.get('hdfs_namenode_principal', None),
            'client_retries' : hdfs_configs.get('client_retries', 10),
            'client_sleep_base_millis' : hdfs_configs.get('client_sleep_base_millis', 500),
            'client_sleep_max_millis' : hdfs_configs.get('client_sleep_max_millis', 15000),
            'socket_timeout_millis' : hdfs_configs.get('socket_timeout_millis', 60000),
            'failover_max_attempts' : hdfs_configs.get('failover_max_attempts', 15),
            'use_datanode_hostname' : hdfs_configs.get('use_datanode_hostname', False),
            'nameservices' : core_configs.get('nameservices', {}),
        }

        if core_configs.get('default_nameservice') in hdfs_configs.get('nameservices', {}):
            defaultns = core_configs['default_nameservice']
            configs['default_nameservice'] = defaultns
            #if core_configs.get('default_scheme') == "viewfs":
            #    configs['nameservices'] = hdfs_configs['nameservices']
            #    configs['nameservices'][defaultns]['default'] = True
            #else:
            #    configs['nameservices'] = { defaultns: {} }
            #    configs['nameservices'][defaultns]['namenodes'] = hdfs_configs['nameservices'][defaultns]['namenodes']
            configs['nameservices'] = hdfs_configs['nameservices']
            configs['nameservices'][defaultns]['default'] = True

        return configs
