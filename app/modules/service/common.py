import os
import http.cookies

import modules.db.sql as sql
import modules.server.ssh as mod_ssh
import modules.common.common as common
import modules.server.server as server_mod
import modules.roxy_wi_tools as roxy_wi_tools

get_config = roxy_wi_tools.GetConfigVar()


def check_haproxy_version(server_ip):
	hap_sock_p = sql.get_setting('haproxy_sock_port')
	ver = ""
	cmd = f"echo 'show info' |nc {server_ip} {hap_sock_p} |grep Version |awk '{{print $2}}'"
	output, stderr = server_mod.subprocess_execute(cmd)
	for line in output:
		ver = line

	return ver


def is_restarted(server_ip: str, action: str) -> None:
	cookie = http.cookies.SimpleCookie(os.environ.get("HTTP_COOKIE"))
	user_uuid = cookie.get('uuid')
	user_role = sql.get_user_role_by_uuid(user_uuid.value)

	if sql.is_serv_protected(server_ip) and int(user_role) > 2:
		print(f'error: This server is protected. You cannot {action} it')
		return


def is_not_allowed_to_restart(server_id: int, service: str) -> None:
	if service != 'waf':
		is_restart = sql.select_service_setting(server_id, service, 'restart')
	else:
		is_restart = 0

	if int(is_restart) == 1:
		print('warning: this service is not allowed to be restarted')
		return


def get_exp_version(server_ip: str, service_name: str) -> str:
	server_ip = common.is_ip_or_dns(server_ip)
	if service_name == 'haproxy_exporter':
		commands = ["/opt/prometheus/exporters/haproxy_exporter --version 2>&1 |head -1|awk '{print $3}'"]
	elif service_name == 'nginx_exporter':
		commands = ["/opt/prometheus/exporters/nginx_exporter 2>&1 |head -1 |awk -F\"=\" '{print $2}'|awk '{print $1}'"]
	elif service_name == 'node_exporter':
		commands = ["node_exporter --version 2>&1 |head -1|awk '{print $3}'"]
	elif service_name == 'apache_exporter':
		commands = ["/opt/prometheus/exporters/apache_exporter --version 2>&1 |head -1|awk '{print $3}'"]

	ver = server_mod.ssh_command(server_ip, commands)

	if ver != '':
		return ver
	else:
		return 'no'


def get_correct_apache_service_name(server_ip=None, server_id=0) -> str:
	if server_id is None:
		server_id = sql.select_server_id_by_ip(server_ip)

	try:
		os_info = sql.select_os_info(server_id)
	except Exception as e:
		raise Exception(f'error: cannot get server info: {e}')

	if "CentOS" in os_info or "Redhat" in os_info:
		return 'httpd'
	else:
		return 'apache2'


def server_status(stdout):
	proc_count = ""

	for line in stdout:
		if "Ncat: " not in line:
			for k in line:
				try:
					proc_count = k.split(":")[1]
				except Exception:
					proc_count = 1
		else:
			proc_count = 0
	return proc_count


def check_haproxy_config(server_ip):
	server_id = sql.select_server_id_by_ip(server_ip=server_ip)
	is_dockerized = sql.select_service_setting(server_id, 'haproxy', 'dockerized')
	config_path = sql.get_setting('haproxy_config_path')

	if is_dockerized == '1':
		container_name = sql.get_setting('haproxy_container_name')
		commands = [f"sudo docker exec -it {container_name} haproxy -q -c -f {config_path}"]
	else:
		commands = [f"haproxy  -q -c -f {config_path}"]

	with mod_ssh.ssh_connect(server_ip) as ssh:
		for command in commands:
			stdin, stdout, stderr = ssh.run_command(command)
			if not stderr.read():
				return True
			else:
				return False


def check_nginx_config(server_ip):
	commands = [f"nginx -q -t -p {sql.get_setting('nginx_dir')}"]

	with mod_ssh.ssh_connect(server_ip) as ssh:
		for command in commands:
			stdin, stdout, stderr = ssh.run_command(command)
			if not stderr.read():
				return True
			else:
				return False


def overview_backends(server_ip: str, service: str) -> None:
	from jinja2 import Environment, FileSystemLoader

	import modules.config.section as section_mod
	import modules.roxywi.common as roxywi_common

	env = Environment(loader=FileSystemLoader('templates/ajax'), autoescape=True)
	template = env.get_template('haproxyservers_backends.html')
	format_file = 'cfg'

	if service == 'haproxy':
		configs_dir = get_config.get_config_var('configs', 'haproxy_save_configs_dir')
		format_file = 'cfg'
	elif service == 'keepalived':
		configs_dir = get_config.get_config_var('configs', 'kp_save_configs_dir')
		format_file = 'conf'

	if service != 'nginx' and service != 'apache':
		try:
			sections = section_mod.get_sections(configs_dir + roxywi_common.get_files(configs_dir, format_file)[0],
												service=service)
		except Exception as e:
			roxywi_common.logging('Roxy-WI server', str(e), roxywi=1)

			try:
				cfg = f"{configs_dir}{server_ip}-{get_date.return_date('config')}.{format_file}"
			except Exception as e:
				roxywi_common.logging('Roxy-WI server', f' Cannot generate a cfg path {e}', roxywi=1)
			try:
				if service == 'keepalived':
					config_mod.get_config(server_ip, cfg, keepalived=1)
				else:
					config_mod.get_config(server_ip, cfg)
			except Exception as e:
				roxywi_common.logging('Roxy-WI server', f' Cannot download a config {e}', roxywi=1)
			try:
				sections = section_mod.get_sections(cfg, service=service)
			except Exception as e:
				roxywi_common.logging('Roxy-WI server', f' Cannot get sections from config file {e}', roxywi=1)
				sections = 'Cannot get backends'
	else:
		sections = section_mod.get_remote_sections(server_ip, service)

	template = template.render(backends=sections, serv=server_ip, service=service)
	print(template)


def get_overview_last_edit(server_ip: str, service: str) -> None:
	if service == 'nginx':
		config_path = sql.get_setting('nginx_config_path')
	elif service == 'keepalived':
		config_path = sql.get_setting('keepalived_config_path')
	else:
		config_path = sql.get_setting('haproxy_config_path')
	commands = ["ls -l %s |awk '{ print $6\" \"$7\" \"$8}'" % config_path]
	try:
		print(server_mod.ssh_command(server_ip, commands))
	except Exception as e:
		print(f'error: Cannot get last date {e} for server {serv}')


def overview_service(server_id: int, name: str, service: str) -> None:
	import asyncio
	from jinja2 import Environment, FileSystemLoader

	async def async_get_overviewServers(serv1, serv2, service):
		if service == 'haproxy':
			cmd = 'echo "show info" |nc %s %s -w 1|grep -e "node\|Nbproc\|Maxco\|MB\|Nbthread"' % (
				serv2, sql.get_setting('haproxy_sock_port'))
			out = server_mod.subprocess_execute(cmd)
			return_out = ""

			for k in out:
				if "Ncat:" not in k:
					for r in k:
						return_out += r
						return_out += "<br />"
				else:
					return_out = "Cannot connect to HAProxy"
		else:
			return_out = ''

		server_status = (serv1, serv2, return_out)
		return server_status

	async def get_runner_overviewServers(**kwargs):
		env = Environment(loader=FileSystemLoader('templates/ajax'),
						  extensions=['jinja2.ext.loopcontrols', 'jinja2.ext.do'])
		template = env.get_template('overviewServers.html')

		servers = []
		cookie = http.cookies.SimpleCookie(os.environ.get("HTTP_COOKIE"))
		user_id = cookie.get('uuid')
		role = sql.get_user_role_by_uuid(user_id.value)
		futures = [async_get_overviewServers(kwargs.get('server1'), kwargs.get('server2'), kwargs.get('service'))]

		for i, future in enumerate(asyncio.as_completed(futures)):
			result = await future
			servers.append(result)
		servers_sorted = sorted(servers, key=common.get_key)
		template = template.render(service_status=servers_sorted, role=role, id=kwargs.get('id'), service_page=service)
		print(template)

	ioloop = asyncio.get_event_loop()
	ioloop.run_until_complete(get_runner_overviewServers(server1=name, server2=serv, id=server_id, service=service))
	ioloop.close()
