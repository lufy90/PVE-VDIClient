#!/usr/bin/env python3
import proxmoxer # pip install proxmoxer
import requests
import tkinter as tk
import customtkinter as ctk
from datetime import datetime
from configparser import ConfigParser
import argparse
import random
import sys
import os
import json
import subprocess
import sys # Added for sys.stderr
import math
import base64
from time import sleep
from io import StringIO, BytesIO



class G:
	spiceproxy_conv = {}
	proxmox = None
	icon = None
	vvcmd = None
	scaling = 1
	#########
	inidebug = False
	addl_params = None
	imagefile = None
	kiosk = False
	viewer_kiosk = True
	fullscreen = True
	show_reset = False
	show_hibernate = False
	current_hostset = 'DEFAULT'
	title = 'VDI Login'
	hosts = {}
	theme = 'LightBlue'
	guest_type = 'both'
	width = None
	height = None
	page_size = 10
	timeout = 15
	config_path = None
	config_type = 'file'
	UI_RADIUS = 0
	TITLE_FONT = {'size': 30, 'weight': 'bold'}
	VM_NAME_FONT = {'size': 24, 'weight': 'bold'}
	DEFAULT_FONT = {'size': 18}
	LABEL_FONT = {'size': 18}
	BUTTON_FONT = {'size': 18}


def resolve_config_file(config_location=None):
	if config_location:
		if os.path.isfile(config_location):
			return config_location
		return None
	if os.name == 'nt':
		config_list = [
			f'{os.getenv("APPDATA")}\\VDIClient\\vdiclient.ini',
			f'{os.getenv("PROGRAMFILES")}\\VDIClient\\vdiclient.ini',
			f'{os.getenv("PROGRAMFILES(x86)")}\\VDIClient\\vdiclient.ini',
			'C:\\Program Files\\VDIClient\\vdiclient.ini'
		]
	elif os.name == 'posix':
		config_list = [
			os.path.expanduser('~/.config/VDIClient/vdiclient.ini'),
			'/etc/vdiclient/vdiclient.ini',
			'/usr/local/etc/vdiclient/vdiclient.ini'
		]
	else:
		config_list = []
	for location in config_list:
		if os.path.exists(location):
			return location
	return None


def get_credentials_file_path():
	if G.config_path:
		return os.path.join(os.path.dirname(os.path.abspath(G.config_path)), 'credentials.json')
	return os.path.join(os.path.expanduser('~/.config/VDIClient'), 'credentials.json')


def _encode_stored_secret(value):
	return base64.b64encode(value.encode('utf-8')).decode('ascii')


def _decode_stored_secret(value):
	if not value:
		return ''
	try:
		return base64.b64decode(value.encode('ascii')).decode('utf-8')
	except Exception:
		return ''


def load_remembered_credentials(group_name):
	path = get_credentials_file_path()
	if not os.path.isfile(path):
		return None
	try:
		with open(path, 'r', encoding='utf-8') as credentials_file:
			data = json.load(credentials_file)
	except Exception:
		return None
	entry = data.get(group_name)
	if not entry or not entry.get('remember'):
		return None
	return {
		'username': entry.get('username', ''),
		'password': _decode_stored_secret(entry.get('password', '')),
	}


def save_remembered_credentials(group_name, username, password, remember):
	path = get_credentials_file_path()
	data = {}
	if os.path.isfile(path):
		try:
			with open(path, 'r', encoding='utf-8') as credentials_file:
				data = json.load(credentials_file)
		except Exception:
			data = {}
	if remember:
		data[group_name] = {
			'remember': True,
			'username': username,
			'password': _encode_stored_secret(password),
		}
	elif group_name in data:
		del data[group_name]
	credentials_dir = os.path.dirname(path)
	if credentials_dir:
		os.makedirs(credentials_dir, exist_ok=True)
	try:
		with open(path, 'w', encoding='utf-8') as credentials_file:
			json.dump(data, credentials_file, indent=2)
		try:
			os.chmod(path, 0o600)
		except Exception:
			pass
	except Exception as e:
		print(f'Unable to save remembered credentials: {e!r}', file=sys.stderr)


def make_config_parser():
	config = ConfigParser(delimiters='=', interpolation=None)
	config.optionxform = str
	return config


def hostpool_to_json(hostpool):
	pool = {}
	for entry in hostpool:
		pool[entry['host']] = int(entry['port'])
	return json.dumps(pool, sort_keys=True)


def validate_host_port(value, label):
	text = value.strip()
	if not text:
		return None, f'{label} is required.'
	if ':' not in text:
		return None, f'{label} must be in host:port format (e.g. pve1.example.com:3128).'
	host_part, port_text = text.rsplit(':', 1)
	if not host_part.strip():
		return None, f'{label} must include a host before the port.'
	try:
		port = int(port_text.strip())
	except ValueError:
		return None, f'{label} port must be a number.'
	if port < 1 or port > 65535:
		return None, f'{label} port must be between 1 and 65535.'
	return f'{host_part.strip()}:{port}', None


def save_settings_to_config(group_name, host_cfg, spiceproxy_conv):
	if not G.config_path:
		return False, 'No configuration file path is set.'
	if G.config_type != 'file':
		return False, 'Settings can only be saved to a local configuration file.'
	if not os.access(G.config_path, os.W_OK):
		return False, f'Configuration file is not writable:\n{G.config_path}'
	config = make_config_parser()
	try:
		config.read(G.config_path, encoding='utf-8')
	except Exception as e:
		return False, f'Unable to read configuration file:\n{e!r}'
	section = f'Hosts.{group_name}'
	if section not in config.sections():
		config.add_section(section)
	config[section]['hostpool'] = hostpool_to_json(host_cfg['hostpool'])
	config[section]['auth_backend'] = host_cfg.get('backend', 'pve')
	config[section]['auth_totp'] = 'true' if host_cfg.get('totp') else 'false'
	config[section]['tls_verify'] = 'true' if host_cfg.get('verify_ssl') else 'false'
	for optional_key, cfg_key in (
		('user', 'user'),
		('token_name', 'token_name'),
		('token_value', 'token_value'),
		('pwresetcmd', 'pwresetcmd'),
	):
		value = host_cfg.get(cfg_key)
		if value:
			config[section][optional_key] = str(value)
		elif optional_key in config[section]:
			del config[section][optional_key]
	if host_cfg.get('auto_vmid') is not None:
		config[section]['auto_vmid'] = str(host_cfg['auto_vmid'])
	elif 'auto_vmid' in config[section]:
		del config[section]['auto_vmid']
	if spiceproxy_conv:
		if 'SpiceProxyRedirect' not in config.sections():
			config.add_section('SpiceProxyRedirect')
		for key in list(config['SpiceProxyRedirect'].keys()):
			del config['SpiceProxyRedirect'][key]
		for from_addr, to_addr in spiceproxy_conv.items():
			config['SpiceProxyRedirect'][from_addr] = to_addr
	elif 'SpiceProxyRedirect' in config.sections():
		config.remove_section('SpiceProxyRedirect')
	try:
		with open(G.config_path, 'w', encoding='utf-8') as config_file:
			config.write(config_file)
	except Exception as e:
		return False, f'Unable to write configuration file:\n{e!r}'
	return True, None


def reload_connection_settings(show_errors=False):
	"""Reload host pool and SPICE proxy settings from the config file."""
	if G.config_type != 'file' or not G.config_path:
		return False
	return loadconfig(
		config_location=G.config_path,
		config_type='file',
		show_errors=show_errors,
		preserve_hostset=True
	)


def open_hostpool_settings():
	if G.config_type != 'file' or not G.config_path:
		win_popup_button('Settings can only be edited when using a local configuration file.', 'OK')
		return False
	if not os.access(G.config_path, os.W_OK):
		win_popup_button(f'Configuration file is not writable:\n{G.config_path}', 'OK')
		return False
	if not loadconfig(config_location=G.config_path, config_type='file', show_errors=True, preserve_hostset=True):
		return False
	if not G.hosts:
		win_popup_button('No host groups are defined in the configuration file.', 'OK')
		return False

	root = get_hidden_root()
	window = VDIWindow(root)
	window.title('VDI Settings')
	set_window_icon(window)
	window.update_idletasks()
	screen_w, screen_h = attach_vdi_window_background(window, fullscreen_geometry=True)
	add_power_button(window)
	panel, settings_wrap = create_settings_content_panel(window, screen_w, screen_h)
	container = ui_frame(panel, fg_color='transparent', border_width=0)
	container.pack(padx=16, pady=16, fill='x')
	ctk.CTkLabel(container, text='VDI Settings', font=get_font('TITLE_FONT')).pack(pady=(0, 6))
	ctk.CTkLabel(
		container,
		text=f'Config: {G.config_path}',
		font=get_font('LABEL_FONT'),
		wraplength=settings_wrap,
		justify='left'
	).pack(anchor='w', pady=(0, 8))
	ctk.CTkLabel(
		container,
		text='Save writes changes to the configuration file. Host pool and SPICE proxy are loaded when you log in.',
		font=get_font('LABEL_FONT'),
		text_color=('gray40', 'gray60'),
		wraplength=settings_wrap,
		justify='left'
	).pack(anchor='w', pady=(0, 12))
	group_names = list(G.hosts.keys())
	selected_group = {'name': G.current_hostset}
	group_combo = None
	if len(group_names) > 1:
		ctk.CTkLabel(container, text='Server Group:', font=get_font('LABEL_FONT')).pack(anchor='w', pady=(0, 4))
		group_combo = ui_combo(container, values=group_names, font=get_font('DEFAULT_FONT'))
		group_combo.set(G.current_hostset)
		group_combo.pack(fill='x', pady=(0, 12))
	ctk.CTkLabel(container, text='Proxmox Hosts (address and API port):', font=get_font('LABEL_FONT')).pack(anchor='w', pady=(0, 4))
	hosts_scroll = ui_frame(container, fg_color='transparent', border_width=0)
	hosts_scroll.pack(fill='x', pady=(0, 8))
	host_rows = []
	add_host_button = ui_button(container, text='Add Host', font=get_font('BUTTON_FONT'))
	add_host_button.pack(anchor='w', pady=(0, 12))
	add_host_button.configure(command=lambda: add_host_row())
	ui_frame(container, height=1, fg_color=('gray70', 'gray30'), border_width=0).pack(fill='x', pady=(0, 12))
	ctk.CTkLabel(
		container,
		text='SPICE proxy redirect (API host:port → reachable host:port):',
		font=get_font('LABEL_FONT'),
		wraplength=settings_wrap,
		justify='left'
	).pack(anchor='w', pady=(0, 4))
	ctk.CTkLabel(
		container,
		text='Use inidebug to read the proxy value from the API, then map it to an address clients can reach.',
		font=get_font('LABEL_FONT'),
		text_color=('gray40', 'gray60'),
		wraplength=settings_wrap,
		justify='left'
	).pack(anchor='w', pady=(0, 8))
	spice_scroll = ui_frame(container, fg_color='transparent', border_width=0)
	spice_scroll.pack(fill='x', pady=(0, 8))
	spice_rows = []
	add_spice_button = ui_button(container, text='Add Redirect Rule', font=get_font('BUTTON_FONT'))
	add_spice_button.pack(anchor='w', pady=(0, 12))
	add_spice_button.configure(command=lambda: add_spice_row())

	def clear_host_rows():
		for row in host_rows:
			row['frame'].destroy()
		host_rows.clear()

	def add_host_row(host='', port='8006'):
		row_frame = ui_frame(hosts_scroll, fg_color='transparent', border_width=0)
		row_frame.pack(fill='x', pady=(0, 6))
		host_entry = ui_entry(row_frame, placeholder_text='Host or FQDN', font=get_font('DEFAULT_FONT'))
		host_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
		if host:
			host_entry.insert(0, host)
		port_entry = ui_entry(row_frame, width=90, placeholder_text='Port', font=get_font('DEFAULT_FONT'))
		port_entry.pack(side='left', padx=(0, 8))
		port_entry.insert(0, str(port))
		remove_button = ui_button(
			row_frame,
			text='Remove',
			width=90,
			font=get_font('BUTTON_FONT'),
			fg_color='#d65f5f',
			hover_color='#d85f5f'
		)
		remove_button.pack(side='right')

		def remove_row():
			if len(host_rows) <= 1:
				win_popup_button('At least one host is required in the pool.', 'OK')
				return
			row_frame.destroy()
			host_rows.remove(row_data)

		row_data = {'frame': row_frame, 'host_entry': host_entry, 'port_entry': port_entry}
		remove_button.configure(command=remove_row)
		host_rows.append(row_data)

	def clear_spice_rows():
		for row in spice_rows:
			row['frame'].destroy()
		spice_rows.clear()

	def add_spice_row(from_addr='', to_addr=''):
		row_frame = ui_frame(spice_scroll, fg_color='transparent', border_width=0)
		row_frame.pack(fill='x', pady=(0, 6))
		from_entry = ui_entry(row_frame, placeholder_text='From (e.g. pve1.example.com:3128)', font=get_font('DEFAULT_FONT'))
		from_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
		if from_addr:
			from_entry.insert(0, from_addr)
		ctk.CTkLabel(row_frame, text='→', font=get_font('LABEL_FONT')).pack(side='left', padx=(0, 8))
		to_entry = ui_entry(row_frame, placeholder_text='To (e.g. 10.0.0.1:6000)', font=get_font('DEFAULT_FONT'))
		to_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
		if to_addr:
			to_entry.insert(0, to_addr)
		remove_button = ui_button(
			row_frame,
			text='Remove',
			width=90,
			font=get_font('BUTTON_FONT'),
			fg_color='#d65f5f',
			hover_color='#d85f5f'
		)
		remove_button.pack(side='right')

		def remove_row():
			row_frame.destroy()
			spice_rows.remove(row_data)

		row_data = {'frame': row_frame, 'from_entry': from_entry, 'to_entry': to_entry}
		remove_button.configure(command=remove_row)
		spice_rows.append(row_data)

	def load_spice_proxy_rules():
		clear_spice_rows()
		for from_addr, to_addr in G.spiceproxy_conv.items():
			add_spice_row(from_addr, to_addr)

	def load_group(group_name):
		selected_group['name'] = group_name
		cfg = G.hosts[group_name]
		clear_host_rows()
		if cfg['hostpool']:
			for entry in cfg['hostpool']:
				add_host_row(entry['host'], entry.get('port', 8006))
		else:
			add_host_row()

	def on_group_change(choice):
		if choice:
			load_group(choice)

	def collect_hostpool():
		hostpool = []
		for row in host_rows:
			host = row['host_entry'].get().strip()
			port_text = row['port_entry'].get().strip()
			if not host and not port_text:
				continue
			if not host:
				return None, 'Each host entry must include an address or FQDN.'
			try:
				port = int(port_text or '8006')
			except ValueError:
				return None, f'Invalid port for host "{host}". Ports must be numbers.'
			if port < 1 or port > 65535:
				return None, f'Invalid port for host "{host}". Use a value between 1 and 65535.'
			hostpool.append({'host': host, 'port': port})
		if not hostpool:
			return None, 'Add at least one Proxmox host to the pool.'
		return hostpool, None

	def collect_spice_proxy_rules():
		rules = {}
		for index, row in enumerate(spice_rows, start=1):
			from_text = row['from_entry'].get().strip()
			to_text = row['to_entry'].get().strip()
			if not from_text and not to_text:
				continue
			from_addr, error = validate_host_port(from_text, f'Redirect rule {index} source')
			if error:
				return None, error
			to_addr, error = validate_host_port(to_text, f'Redirect rule {index} target')
			if error:
				return None, error
			if from_addr in rules:
				return None, f'Duplicate SPICE proxy source: {from_addr}'
			rules[from_addr] = to_addr
		return rules, None

	def do_save():
		hostpool, error = collect_hostpool()
		if error:
			win_popup_button(error, 'OK')
			return
		spiceproxy_conv, error = collect_spice_proxy_rules()
		if error:
			win_popup_button(error, 'OK')
			return
		group_name = group_combo.get() if group_combo else selected_group['name']
		host_cfg = dict(G.hosts[group_name])
		host_cfg['hostpool'] = hostpool
		ok, save_error = save_settings_to_config(group_name, host_cfg, spiceproxy_conv)
		if not ok:
			win_popup_button(save_error, 'OK')
			return
		try:
			window.grab_release()
		except tk.TclError:
			pass
		win_popup_button('Settings saved.', 'OK')
		window.destroy()

	def do_cancel():
		window.destroy()

	button_frame = ui_frame(container, fg_color='transparent', border_width=0)
	button_frame.pack(fill='x', pady=(6, 0))
	ui_button(button_frame, text='Save', font=get_font('BUTTON_FONT'), command=do_save).pack(
		side='left', expand=True, fill='x', padx=(0, 8)
	)
	ui_button(button_frame, text='Cancel', font=get_font('BUTTON_FONT'), command=do_cancel).pack(
		side='left', expand=True, fill='x'
	)
	if group_combo:
		group_combo.configure(command=on_group_change)
	load_group(G.current_hostset)
	load_spice_proxy_rules()
	window.update_idletasks()
	apply_kiosk_state(window)
	if not G.kiosk:
		window.grab_set()
	window.protocol('WM_DELETE_WINDOW', do_cancel)
	window.wait_window()
	return False


def loadconfig(config_location = None, config_type='file', config_username = None, config_password = None, ssl_verify = True, show_errors=True, preserve_hostset=False):
	previous_group = G.current_hostset if preserve_hostset else None
	G.config_type = config_type
	G.config_path = None
	G.hosts = {}
	G.spiceproxy_conv = {}
	G.addl_params = None
	config = make_config_parser()
	if config_type == 'file':
		if config_location:
			if not os.path.isfile(config_location):
				if show_errors:
					win_popup_button(f'Unable to read supplied configuration:\n{config_location} does not exist!', 'OK')
				return False
			G.config_path = config_location
		else:
			G.config_path = resolve_config_file()
			if not G.config_path:
				if show_errors:
					win_popup_button('Unable to read supplied configuration from any location!', 'OK')
				return False
			config_location = G.config_path
		try:
			config.read(config_location, encoding='utf-8')
		except Exception as e:
			if show_errors:
				win_popup_button(f'Unable to read configuration file:\n{e!r}', 'OK')
			return False
	elif config_type == 'http':
		if not config_location:
			if show_errors:
				win_popup_button('--config_type http defined, yet no URL provided in --config_location parameter!', 'OK')
			return False
		try:
			if config_username and config_password:
				r = requests.get(url=config_location, auth=(config_username, config_password), verify = ssl_verify)
			else:
				r = requests.get(url=config_location, verify = ssl_verify)
			config.read_string(r.text)
		except Exception as e:
			if show_errors:
				win_popup_button(f"Unable to read configuration from URL!\n{e}", "OK")
			return False
	if not 'General' in config:
		if show_errors:
			win_popup_button('Unable to read supplied configuration:\nNo `General` section defined!', 'OK')
		return False
	else:
		if 'title' in config['General']:
			G.title = config['General']['title']
		if 'theme' in config['General']:
			G.theme = config['General']['theme']
		if 'icon' in config['General']:
			if os.path.exists(config['General']['icon']):
				G.icon = config['General']['icon']
		if 'logo' in config['General']:
			if os.path.exists(config['General']['logo']):
				G.imagefile = config['General']['logo']
		if 'kiosk' in config['General']:
			G.kiosk = config['General'].getboolean('kiosk')
		if 'viewer_kiosk' in config['General']:
			G.viewer_kiosk = config['General'].getboolean('viewer_kiosk')
		if 'fullscreen' in config['General']:
			G.fullscreen = config['General'].getboolean('fullscreen')
		if 'inidebug' in config['General']:
			G.inidebug = config['General'].getboolean('inidebug')
		if 'guest_type' in config['General']:
			G.guest_type = config['General']['guest_type']
		if 'show_reset' in config['General']:
			G.show_reset = config['General'].getboolean('show_reset')
		if 'window_width' in config['General']:
			G.width = config['General'].getint('window_width')
		if 'window_height' in config['General']:
			G.height = config['General'].getint('window_height')
		if 'page_size' in config['General']:
			G.page_size = config['General'].getint('page_size')
		if 'timeout' in config['General']:
			G.timeout = config['General'].getint('timeout')
	
	has_new_host_sections = any(section.startswith('Hosts.') for section in config.sections())
	if 'Authentication' in config and not has_new_host_sections: # Legacy configuration
		G.hosts['DEFAULT'] = {
			'hostpool' : [],
			'backend' : 'pve',
			'user' : "",
			'token_name' : None,
			'token_value' : None,
			'totp' : False,
			'verify_ssl' : True,
			'pwresetcmd' : None,
			'auto_vmid' : None,
			'knock_seq': []
		}
		if not 'Hosts' in config:
			if show_errors:
				win_popup_button(f'Unable to read supplied configuration:\nNo `Hosts` section defined!', 'OK')
			return False
		for key in config['Hosts']:
			G.hosts['DEFAULT']['hostpool'].append({
				'host': key,
				'port': int(config['Hosts'][key])
			})
		if 'auth_backend' in config['Authentication']:
			G.hosts['DEFAULT']['backend'] = config['Authentication']['auth_backend']
		if 'user' in config['Authentication']:
			G.hosts['DEFAULT']['user'] = config['Authentication']['user']
		if 'token_name' in config['Authentication']:
			G.hosts['DEFAULT']['token_name'] = config['Authentication']['token_name']
		if 'token_value' in config['Authentication']:
			G.hosts['DEFAULT']['token_value'] = config['Authentication']['token_value']
		if 'auth_totp' in config['Authentication']:
			G.hosts['DEFAULT']['totp'] = config['Authentication'].getboolean('auth_totp')
		if 'tls_verify' in config['Authentication']:
			G.hosts['DEFAULT']['verify_ssl'] = config['Authentication'].getboolean('tls_verify')
		if 'pwresetcmd' in config['Authentication']:
			G.hosts['DEFAULT']['pwresetcmd'] = config['Authentication']['pwresetcmd']
		if 'auto_vmid' in config['Authentication']:
			G.hosts['DEFAULT']['auto_vmid'] = config['Authentication'].getint('auto_vmid')
		if 'knock_seq' in config['Authentication']:
			try:
				G.hosts['DEFAULT']['knock_seq'] = json.loads(config['Authentication']['knock_seq'])
			except Exception as e:
				if show_errors:
					win_popup_button(f'Knock sequence not valid JSON, skipping!\n{e!r}', 'OK')
	elif has_new_host_sections: # New style config
		for section in config.sections():
			if section.startswith('Hosts.'):
				_, group = section.split('.', 1)
				G.hosts[group] = {
					'hostpool' : [],
					'backend' : 'pve',
					'user' : "",
					'token_name' : None,
					'token_value' : None,
					'totp' : False,
					'verify_ssl' : True,
					'pwresetcmd' : None,
					'auto_vmid' : None,
					'knock_seq': []
				}
				try:
					hostjson = json.loads(config[section]['hostpool'].strip())
				except Exception as e:
					if show_errors:
						win_popup_button(f"Error: could not parse hostpool in section {section}:\n{e!r}", "OK")
					return False
				for key, value in hostjson.items():
					G.hosts[group]['hostpool'].append({
						'host': key,
						'port': int(value)
					})
				if 'auth_backend' in config[section]:
					G.hosts[group]['backend'] = config[section]['auth_backend']
				if 'user' in config[section]:
					G.hosts[group]['user'] = config[section]['user']
				if 'token_name' in config[section]:
					G.hosts[group]['token_name'] = config[section]['token_name']
				if 'token_value' in config[section]:
					G.hosts[group]['token_value'] = config[section]['token_value']
				if 'auth_totp' in config[section]:
					G.hosts[group]['totp'] = config[section].getboolean('auth_totp')
				if 'tls_verify' in config[section]:
					G.hosts[group]['verify_ssl'] = config[section].getboolean('tls_verify')
				if 'pwresetcmd' in config[section]:
					G.hosts[group]['pwresetcmd'] = config[section]['pwresetcmd']
				if 'auto_vmid' in config[section]:
					G.hosts[group]['auto_vmid'] = config[section].getint('auto_vmid')
				if 'knock_seq' in config[section]:
					try:
						G.hosts[group]['knock_seq'] = json.loads(config[section]['knock_seq'])
					except Exception as e:
						if show_errors:
							win_popup_button(f'Knock sequence not valid JSON, skipping!\n{e!r}', 'OK')
		if not G.hosts:
			if show_errors:
				win_popup_button('Unable to read supplied configuration:\nNo `Hosts.*` sections defined!', 'OK')
			return False
		if previous_group and previous_group in G.hosts:
			G.current_hostset = previous_group
		else:
			G.current_hostset = next(iter(G.hosts))
	if 'SpiceProxyRedirect' in config:
		for key in config['SpiceProxyRedirect']:
			G.spiceproxy_conv[key] = config['SpiceProxyRedirect'][key]
	if 'AdditionalParameters' in config:
		G.addl_params = {}
		for key in config['AdditionalParameters']:
			G.addl_params[key] = config['AdditionalParameters'][key]
	return True

def get_hidden_root():
	if getattr(G, '_hidden_root', None) is None:
		ctk.set_default_color_theme('blue')
		root = ctk.CTk()
		root.withdraw()
		G._hidden_root = root
	return G._hidden_root


def get_screen_dimensions():
	"""Monitor size from the application root (not a child toplevel)."""
	root = get_hidden_root()
	root.update_idletasks()
	return root.winfo_screenwidth(), root.winfo_screenheight()


def apply_theme():
	theme = str(G.theme).strip().lower() if G.theme else ''
	if 'dark' in theme:
		ctk.set_appearance_mode('Dark')
	elif 'light' in theme:
		ctk.set_appearance_mode('Light')
	else:
		ctk.set_appearance_mode('System')
	ctk.set_default_color_theme('blue')


def ui_frame(master, **kwargs):
	kwargs.setdefault('corner_radius', G.UI_RADIUS)
	return ctk.CTkFrame(master, **kwargs)


def ui_button(master, **kwargs):
	kwargs.setdefault('corner_radius', G.UI_RADIUS)
	return ctk.CTkButton(master, **kwargs)


def ui_entry(master, **kwargs):
	kwargs.setdefault('corner_radius', G.UI_RADIUS)
	return ctk.CTkEntry(master, **kwargs)


def ui_combo(master, **kwargs):
	kwargs.setdefault('corner_radius', G.UI_RADIUS)
	kwargs.setdefault('button_corner_radius', G.UI_RADIUS)
	kwargs.setdefault('dropdown_corner_radius', G.UI_RADIUS)
	return ctk.CTkComboBox(master, **kwargs)


def ui_checkbox(master, **kwargs):
	kwargs.setdefault('corner_radius', G.UI_RADIUS)
	return ctk.CTkCheckBox(master, **kwargs)


def ui_scrollable(master, **kwargs):
	kwargs.setdefault('corner_radius', G.UI_RADIUS)
	return ctk.CTkScrollableFrame(master, **kwargs)


def ui_textbox(master, **kwargs):
	kwargs.setdefault('corner_radius', G.UI_RADIUS)
	return ctk.CTkTextbox(master, **kwargs)


def get_app_background_color():
	theme = str(G.theme).strip().lower() if G.theme else ''
	if 'light' in theme:
		return ('#f2f4f7', '#f2f4f7')
	if 'dark' in theme:
		return ('#2c3038', '#2c3038')
	return ('#f2f4f7', '#2c3038')


def attach_vdi_window_background(window, fullscreen_geometry=True):
	"""Paint a solid full-screen background; return monitor width and height."""
	screen_w, screen_h = get_screen_dimensions()
	if fullscreen_geometry:
		window.geometry(f'{screen_w}x{screen_h}+0+0')
		window.update_idletasks()
	bg_color = get_app_background_color()
	window.configure(fg_color=bg_color)
	bg = ui_frame(window, fg_color=bg_color, border_width=0)
	bg.place(relx=0, rely=0, relwidth=1, relheight=1)
	window._vdi_bg_layer = bg
	return screen_w, screen_h


def _run_system_command(command_lists, action_label):
	for command in command_lists:
		try:
			subprocess.Popen(command)
			return True
		except (OSError, FileNotFoundError):
			continue
	win_popup_button(f'Unable to {action_label}:\nRequired system command was not found.', 'OK')
	return False


def system_reboot():
	if os.name == 'nt':
		commands = [['shutdown', '/r', '/t', '0']]
	else:
		commands = [
			['systemctl', 'reboot'],
			['loginctl', 'reboot'],
			['shutdown', '-r', 'now'],
		]
	return _run_system_command(commands, 'reboot')


def system_poweroff():
	if os.name == 'nt':
		commands = [['shutdown', '/s', '/t', '0']]
	else:
		commands = [
			['systemctl', 'poweroff'],
			['loginctl', 'poweroff'],
			['shutdown', '-h', 'now'],
		]
	return _run_system_command(commands, 'shut down')


def ask_yes_no(message, title='Confirm'):
	root = get_hidden_root()
	window = VDIWindow(root)
	window.title(title)
	window.resizable(False, False)
	panel = ui_frame(window, border_width=1, border_color=('gray80', 'gray30'))
	panel.pack(padx=16, pady=16, fill='both', expand=True)
	ctk.CTkLabel(panel, text=message, wraplength=380, justify='center', font=get_font('LABEL_FONT')).pack(
		padx=10, pady=(10, 14)
	)
	button_row = ui_frame(panel, fg_color='transparent', border_width=0)
	button_row.pack(fill='x', pady=(0, 6))
	result = {'value': False}

	def on_yes():
		result['value'] = True
		window.destroy()

	def on_no():
		window.destroy()

	ui_button(button_row, text='Yes', font=get_font('BUTTON_FONT'), command=on_yes).pack(
		side='left', expand=True, fill='x', padx=(0, 8)
	)
	ui_button(button_row, text='No', font=get_font('BUTTON_FONT'), command=on_no).pack(
		side='left', expand=True, fill='x'
	)
	center_window(window)
	window.lift()
	window.focus_force()
	if not G.kiosk:
		window.grab_set()
	window.wait_window()
	return result['value']


def show_power_dialog():
	root = get_hidden_root()
	window = VDIWindow(root)
	window.title('Power')
	window.resizable(False, False)
	panel = ui_frame(window, border_width=1, border_color=('gray80', 'gray30'))
	panel.pack(padx=16, pady=16, fill='both', expand=True)
	ctk.CTkLabel(panel, text='Select a power action:', font=get_font('LABEL_FONT')).pack(pady=(10, 14))

	def do_reboot():
		window.destroy()
		if ask_yes_no('Reboot this device now?', 'Reboot'):
			system_reboot()

	def do_poweroff():
		window.destroy()
		if ask_yes_no('Shut down this device now?', 'Power Off'):
			system_poweroff()

	ui_button(panel, text='Reboot', font=get_font('BUTTON_FONT'), command=do_reboot).pack(fill='x', pady=(0, 8))
	ui_button(panel, text='Power Off', font=get_font('BUTTON_FONT'), command=do_poweroff).pack(fill='x', pady=(0, 8))
	ui_button(panel, text='Cancel', font=get_font('BUTTON_FONT'), command=window.destroy).pack(fill='x', pady=(0, 10))
	center_window(window)
	apply_kiosk_state(window)
	window.lift()
	window.focus_force()
	if not G.kiosk:
		window.grab_set()
	window.wait_window()


def add_power_button(window):
	button = ui_button(
		window,
		text='Power',
		width=88,
		height=36,
		font=get_font('BUTTON_FONT'),
		fg_color=('gray75', 'gray32'),
		hover_color=('gray65', 'gray40'),
		command=show_power_dialog
	)
	button.place(relx=1.0, rely=1.0, anchor='se', x=-20, y=-20)
	button.lift()
	window._power_button = button


def create_settings_content_panel(window, screen_w, screen_h):
	"""Settings panel wider than login, sized to fit content height (no scrollbar)."""
	login_panel_width = 440
	panel_w = min(max(680, int(screen_w * 0.52)), screen_w - 32)
	panel_w = max(panel_w, login_panel_width + 80)
	margin_x = max(16, (screen_w - panel_w) // 2)
	panel = ui_frame(
		window,
		fg_color=('gray96', 'gray17'),
		border_width=1,
		border_color=('gray78', 'gray28'),
	)
	panel.pack(padx=margin_x, pady=40)
	panel.lift()
	return panel, max(400, panel_w - 80)


def prepare_vdi_window(window, fullscreen=None, panel_width=520, panel_height=None):
	"""Fill the window with a solid background and return a plain content panel."""
	if fullscreen is None:
		fullscreen = G.fullscreen or G.kiosk
	window.update_idletasks()
	screen_w, screen_h = attach_vdi_window_background(window, fullscreen_geometry=fullscreen)
	panel_kwargs = {
		'fg_color': ('gray96', 'gray17'),
		'border_width': 1,
		'border_color': ('gray78', 'gray28'),
	}
	if fullscreen:
		panel_kwargs['width'] = panel_width
		panel_kwargs['height'] = panel_height if panel_height else (screen_h - 48)
		panel = ui_frame(window, **panel_kwargs)
		panel.place(relx=0.5, rely=0.5, anchor='center')
		panel.lift()
		add_power_button(window)
	else:
		panel = ui_frame(window, **panel_kwargs)
		panel.pack(padx=24, pady=24, fill='both', expand=True)
	return panel


_font_cache = {}

def get_font(name):
	font_def = getattr(G, name, None)
	if isinstance(font_def, dict):
		if name not in _font_cache:
			_font_cache[name] = ctk.CTkFont(**font_def)
		return _font_cache[name]
	return font_def


def center_window(window):
	window.deiconify()
	window.update_idletasks()
	width = window.winfo_reqwidth()
	height = window.winfo_reqheight()
	screen_width = window.winfo_screenwidth()
	screen_height = window.winfo_screenheight()
	x = max(0, (screen_width - width) // 2)
	y = max(0, (screen_height - height) // 2)
	window.geometry(f"{width}x{height}+{x}+{y}")
	window.update()

class VDIWindow(ctk.CTkToplevel):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)

def apply_kiosk_state(window):
	if G.kiosk:
		window.update()
		# Set window type to 'toolbar' - this is an X11 hint that often removes decorations
		# and taskbar entries, but behavior can vary by WM.
		try:
			window.attributes('-type', 'toolbar')
		except tk.TclError:
			# Fallback if '-type' is not supported (e.g., some macOS versions or older Tk)
			pass
		# Disable the Close button functionality
		window.protocol("WM_DELETE_WINDOW", lambda: None)
		# Disable Maximize and Resize functionality
		window.resizable(False, False)
		# Prevent moving: Store the current centered position
		fixed_x = window.winfo_x()
		fixed_y = window.winfo_y()
		def lock_position(event):
			if not window.winfo_exists():
				return
			# If the window moves away from its fixed position, snap it back
			if window.winfo_x() != fixed_x or window.winfo_y() != fixed_y:
				window.geometry(f"+{fixed_x}+{fixed_y}")
		# Bind to Configure (move/resize events) to effectively "disable" the title bar drag
		window.bind("<Configure>", lock_position, add="+")

def load_image(path, for_ctk_label=False, size=None):
	if not path or not os.path.exists(path):
		return None
	if for_ctk_label:
		try:
			from PIL import Image
			image = Image.open(path).convert("RGBA")
			if size:
				image = image.resize(size, Image.ANTIALIAS)
			return ctk.CTkImage(light_image=image, dark_image=image, size=image.size if size is None else size)
		except Exception:
			try:
				return tk.PhotoImage(file=path)
			except Exception:
				if path.lower().endswith('.ico'):
					try:
						from PIL import Image
						image = Image.open(path).convert("RGBA")
						if size:
							image = image.resize(size, Image.ANTIALIAS)
						buf = BytesIO()
						image.save(buf, format='PNG')
						data = base64.b64encode(buf.getvalue()).decode('ascii')
						return tk.PhotoImage(data=data)
					except Exception:
						return None
				return None
	try:
		return tk.PhotoImage(file=path)
	except Exception:
		if path.lower().endswith('.ico'):
			try:
				from PIL import Image
				image = Image.open(path).convert("RGBA")
				if size:
					image = image.resize(size, Image.ANTIALIAS)
				buf = BytesIO()
				image.save(buf, format='PNG')
				data = base64.b64encode(buf.getvalue()).decode('ascii')
				return tk.PhotoImage(data=data)
			except Exception:
				return None
		return None


def set_window_icon(window):
	if not G.icon or not os.path.exists(G.icon):
		return
	def _apply_icon():
		try:
			if os.name == 'nt' and G.icon.lower().endswith('.ico'):
				# Use the native Windows method for .ico files to ensure taskbar/titlebar consistency
				window.wm_iconbitmap(G.icon)
			else:
				icon = load_image(G.icon)
				if icon:
					# iconphoto(True, ...) sets the icon for this window and future windows
					window.iconphoto(True, icon)
					window._icon_image = icon # Keep a reference to prevent garbage collection
		except Exception:
			pass
	# Delaying execution by 200ms allows CustomTkinter to finish its own icon initialization first
	window.after(200, _apply_icon)


def win_popup(message):
	root = get_hidden_root()
	window = VDIWindow(root)
	# Remove all window decorations and disable interaction functions (moving/closing)
	window.overrideredirect(True)
	window.attributes("-topmost", True)
	window.protocol("WM_DELETE_WINDOW", lambda: None)

	frame = ui_frame(window, border_width=1, border_color=('gray80', 'gray30'))
	frame.pack(padx=12, pady=12, fill='both', expand=True)
	ctk.CTkLabel(frame, text=message, wraplength=420, justify='center', font=get_font('LABEL_FONT')).pack(padx=10, pady=(10, 14))
	window.close = window.destroy
	center_window(window)
	window.lift()
	window.focus_force()
	window.update()
	return window


def win_popup_button(message, button):
	root = get_hidden_root()
	window = VDIWindow(root)
	window.title('')
	window.resizable(False, False)
	panel = prepare_vdi_window(window, fullscreen=False, panel_width=460)
	frame = ui_frame(panel, fg_color='transparent', border_width=0)
	frame.pack(padx=12, pady=12, fill='both', expand=True)
	ctk.CTkLabel(frame, text=message, wraplength=420, justify='center', font=get_font('LABEL_FONT')).pack(padx=10, pady=(10, 14))

	def close_and_destroy():
		window.after(0, window.destroy)

	ui_button(frame, text=button, command=close_and_destroy, font=get_font('BUTTON_FONT')).pack(padx=10, pady=(0, 10))
	center_window(window)
	apply_kiosk_state(window)
	window.lift()
	window.focus_force()
	if not G.kiosk:
		window.grab_set()
	try:
		window.wait_visibility(window)
	except tk.TclError:
		pass
	window.update()
	try:
		window.wait_window()
	except Exception:
		pass


def _build_login_window():
	root = get_hidden_root()
	window = VDIWindow(root)
	window.title(G.title)
	set_window_icon(window)
	panel = prepare_vdi_window(window, fullscreen=True, panel_width=440)
	container = ui_frame(panel, fg_color='transparent', border_width=0)
	container.pack(padx=24, pady=24, fill='both', expand=True)
	if G.imagefile:
		image = load_image(G.imagefile, for_ctk_label=True)
		if image:
			logo = ctk.CTkLabel(container, image=image, text='', fg_color='transparent')
			logo.image = image
			logo.pack(pady=(0, 12))
	ctk.CTkLabel(container, text=G.title, font=get_font('TITLE_FONT')).pack(pady=(0, 16))
	group_combo = None
	if len(G.hosts) > 1:
		groups = list(G.hosts.keys())
		ctk.CTkLabel(container, text='Server Group:', font=get_font('LABEL_FONT')).pack(anchor='w', pady=(0, 4))
		group_combo = ui_combo(container, values=groups, font=get_font('DEFAULT_FONT'))
		group_combo.set(G.current_hostset)
		group_combo.pack(fill='x', pady=(0, 14))
	ctk.CTkLabel(container, text='Username', font=get_font('LABEL_FONT')).pack(anchor='w', pady=(0, 4))
	username_entry = ui_entry(container, placeholder_text='Username', font=get_font('DEFAULT_FONT'))
	remember_var = tk.BooleanVar(value=False)
	saved_creds = load_remembered_credentials(G.current_hostset)
	if saved_creds:
		remember_var.set(True)
		username_entry.insert(0, saved_creds.get('username', ''))
	else:
		username_entry.insert(0, G.hosts[G.current_hostset]['user'] or '')
	username_entry.pack(fill='x', pady=(0, 12))
	username_entry.focus_set()
	ctk.CTkLabel(container, text='Password', font=get_font('LABEL_FONT')).pack(anchor='w', pady=(0, 4))
	password_entry = ui_entry(container, placeholder_text='Password', show='*', font=get_font('DEFAULT_FONT'))
	if saved_creds:
		password_entry.insert(0, saved_creds.get('password', ''))
	password_entry.pack(fill='x', pady=(0, 8))
	ui_checkbox(container, text='Remember password', variable=remember_var, font=get_font('LABEL_FONT')).pack(
		anchor='w', pady=(0, 12)
	)
	totp_entry = None
	if G.hosts[G.current_hostset]['totp']:
		ctk.CTkLabel(container, text='OTP Key', font=get_font('LABEL_FONT')).pack(anchor='w', pady=(0, 4))
		totp_entry = ui_entry(container, placeholder_text='TOTP code', font=get_font('DEFAULT_FONT'))
		totp_entry.pack(fill='x', pady=(0, 12))
	button_frame = ui_frame(container, fg_color='transparent', border_width=0)
	button_frame.pack(fill='x', pady=(6, 0))
	login_button = ui_button(button_frame, text='Log In', font=get_font('BUTTON_FONT'))
	login_button.pack(side='left', expand=True, fill='x', padx=(0, 8 if not G.kiosk else 0))
	cancel_button = None
	if not G.kiosk:
		cancel_button = ui_button(button_frame, text='Cancel', font=get_font('BUTTON_FONT'))
		cancel_button.pack(side='left', expand=True, fill='x')
	pwreset_button = None
	if G.hosts[G.current_hostset]['pwresetcmd']:
		pwreset_button = ui_button(container, text='Password Reset', font=get_font('BUTTON_FONT'))
		pwreset_button.pack(fill='x', pady=(12, 0))
	settings_button = None
	if not G.kiosk and G.config_type == 'file' and G.config_path:
		settings_button = ui_button(container, text='VDI Settings', font=get_font('BUTTON_FONT'))
		settings_button.pack(fill='x', pady=(12, 0))
	window.deiconify()
	apply_kiosk_state(window)
	username_entry.focus_set() # Ensure username entry gets focus
	window_data_entry = username_entry
	return {
		'window': window,
		'group_combo': group_combo,
		'username_entry': username_entry,
		'password_entry': password_entry,
		'remember_var': remember_var,
		'totp_entry': totp_entry,
		'login_button': login_button,
		'cancel_button': cancel_button,
		'pwreset_button': pwreset_button,
		'settings_button': settings_button
	}


def getvms(listonly = False):
	vms = []
	try:
		nodes = []
		for node in G.proxmox.cluster.resources.get(type='node'):
			if node['status'] == 'online':
				nodes.append(node['node'])

		for vm in G.proxmox.cluster.resources.get(type='vm'):
			if vm['node'] not in nodes:
				continue
			if 'template' in vm and vm['template']:
				continue
			if G.guest_type == 'both' or G.guest_type == vm['type']:
				if listonly:
					vms.append(
						{
							'vmid': vm['vmid'],
							'name': vm['name'],
							'node': vm['node']
						}
					)
				else:
					vms.append(vm)
		return vms
	except proxmoxer.core.ResourceException as e:
		win_popup_button(f"Unable to display list of VMs:\n {e!r}", 'OK')
		return False
	except requests.exceptions.RequestException as e: # Catch all requests-related exceptions
		print(f"Network error when querying Proxmox during VM refresh: {e!r}", file=sys.stderr)
		return False
	except Exception as e: # Catch any other unexpected errors
		print(f"An unexpected error occurred in getvms: {e!r}", file=sys.stderr)
		return False


def iniwin(inistring):
	root = get_hidden_root()
	window = VDIWindow(root)
	window.title('INI debug')
	set_window_icon(window)
	panel = prepare_vdi_window(window, fullscreen=False, panel_width=850, panel_height=550)
	text_box = ui_textbox(panel, width=820, height=460, font=get_font('DEFAULT_FONT'))
	text_box.pack(padx=15, pady=(15, 8), fill='both', expand=True)
	text_box.insert('0.0', inistring)
	text_box.configure(state='disabled')
	ui_button(panel, text='Close', command=window.destroy, font=get_font('BUTTON_FONT')).pack(pady=(0, 15))
	center_window(window)
	if not G.kiosk:
		window.grab_set()
	apply_kiosk_state(window)
	window.wait_window()
	return True

def vmaction(vmnode, vmid, vmtype, action='connect'):
	status = False
	if vmtype == 'qemu':
		vmstatus = G.proxmox.nodes(vmnode).qemu(str(vmid)).status.get('current')
	else: # Not sure this is even a thing, but here it is...
		vmstatus = G.proxmox.nodes(vmnode).lxc(str(vmid)).status.get('current')
	if action == 'reload':
		stoppop = win_popup(f'Stopping {vmstatus["name"]}...')
		sleep(.1)
		try:
			if vmtype == 'qemu':
				jobid = G.proxmox.nodes(vmnode).qemu(str(vmid)).status.stop.post(timeout=28)
			else: # Not sure this is even a thing, but here it is...
				jobid = G.proxmox.nodes(vmnode).lxc(str(vmid)).status.stop.post(timeout=28)
		except proxmoxer.core.ResourceException as e:
			stoppop.close()
			win_popup_button(f"Unable to stop VM, please provide your system administrator with the following error:\n {e!r}", 'OK')
			return False
		running = True
		i = 0
		while running and i < 30:
			try:
				jobstatus = G.proxmox.nodes(vmnode).tasks(jobid).status.get()
			except Exception:
				# We ran into a query issue here, going to skip this round and try again
				jobstatus = {}
			if 'exitstatus' in jobstatus:
				stoppop.close()
				stoppop = None
				if jobstatus['exitstatus'] != 'OK':
					win_popup_button('Unable to stop VM, please contact your system administrator for assistance', 'OK')
					return False
				else:
					running = False
					status = True
			sleep(1)
			i += 1
		if not status:
			if stoppop:
				stoppop.close()
			return status
	status = False
	if vmtype == 'qemu':
		vmstatus = G.proxmox.nodes(vmnode).qemu(str(vmid)).status.get('current')
	else: # Not sure this is even a thing, but here it is...
		vmstatus = G.proxmox.nodes(vmnode).lxc(str(vmid)).status.get('current')
	sleep(.2)
	if vmstatus['status'] != 'running':
		startpop = win_popup(f'Starting {vmstatus["name"]}...')
		sleep(.1)
		try:
			if vmtype == 'qemu':
				jobid = G.proxmox.nodes(vmnode).qemu(str(vmid)).status.start.post(timeout=28)
			else: # Not sure this is even a thing, but here it is...
				jobid = G.proxmox.nodes(vmnode).lxc(str(vmid)).status.start.post(timeout=28)
		except proxmoxer.core.ResourceException as e:
			startpop.close()
			win_popup_button(f"Unable to start VM, please provide your system administrator with the following error:\n {e!r}", 'OK')
			return False
		running = False
		i = 0
		while running == False and i < 30:
			try:
				jobstatus = G.proxmox.nodes(vmnode).tasks(jobid).status.get()
			except Exception:
				# We ran into a query issue here, going to skip this round and try again
				jobstatus = {}
			if 'exitstatus' in jobstatus:
				startpop.close()
				startpop = None
				if jobstatus['exitstatus'] != 'OK':
					win_popup_button('Unable to start VM, please contact your system administrator for assistance', 'OK')
					running = True
				else:
					running = True
					status = True
			sleep(1)
			i += 1
		if not status:
			if startpop:
				startpop.close()
			return status
	if action == 'reload':
		return
	try:
		if vmtype == 'qemu':
			spiceconfig = G.proxmox.nodes(vmnode).qemu(str(vmid)).spiceproxy.post()
		else: # Not sure this is even a thing, but here it is...
			spiceconfig = G.proxmox.nodes(vmnode).lxc(str(vmid)).spiceproxy.post()
	except proxmoxer.core.ResourceException as e:
		win_popup_button(f"Unable to connect to VM {vmid}:\n{e!r}\nIs SPICE display configured for your VM?", 'OK')
		return False
	confignode = ConfigParser()
	confignode['virt-viewer'] = {}
	for key, value in spiceconfig.items():
		if key == 'proxy':
			val = value[7:].lower()
			if val in G.spiceproxy_conv:
				confignode['virt-viewer'][key] = f'http://{G.spiceproxy_conv[val]}'
			else:
				confignode['virt-viewer'][key] = f'{value}'
		else:
			confignode['virt-viewer'][key] = f'{value}'
	if G.addl_params:
		for key, value in G.addl_params.items():
			confignode['virt-viewer'][key] = f'{value}'
	inifile = StringIO('')
	confignode.write(inifile)
	inifile.seek(0)
	inistring = inifile.read()
	if G.inidebug:
		closed = iniwin(inistring)
	connpop = win_popup(f'Connecting to {vmstatus["name"]}...')
	pcmd = [G.vvcmd]
	if G.kiosk and G.viewer_kiosk:
		pcmd.append('--kiosk')
		pcmd.append('--kiosk-quit')
		pcmd.append('on-disconnect')
	elif G.fullscreen:
		pcmd.append('--full-screen')
	pcmd.append('-') #We need it to listen on stdin
	process = subprocess.Popen(pcmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
	try:
		output = process.communicate(input=inistring.encode('utf-8'), timeout=5)[0]
	except subprocess.TimeoutExpired:
		pass
	status = True
	connpop.close()
	return status


def setcmd():
	try:
		if os.name == 'nt': # Windows
			import csv
			cmd1 = 'ftype VirtViewer.vvfile'
			result = subprocess.check_output(cmd1, shell=True)
			cmdresult = result.decode('utf-8')
			cmdparts = cmdresult.split('=')
			for row in csv.reader([cmdparts[1]], delimiter = ' ', quotechar = '"'):
				G.vvcmd = row[0]
				break

		elif os.name == 'posix':
			cmd1 = 'which remote-viewer'
			result = subprocess.check_output(cmd1, shell=True)
			G.vvcmd = 'remote-viewer'
	except subprocess.CalledProcessError:
		if os.name == 'nt':
			win_popup_button('Installation of virt-viewer missing, please install from https://virt-manager.org/download/', 'OK')
		elif os.name == 'posix':
			win_popup_button('Installation of virt-viewer missing, please install using `apt install virt-viewer`', 'OK')
		sys.exit()

def pveauth(username, passwd=None, totp=None):
	random.shuffle(G.hosts[G.current_hostset]['hostpool'])
	err = None
	for hostinfo in G.hosts[G.current_hostset]['hostpool']:
		host = hostinfo['host']
		if 'port' in hostinfo:
			port = hostinfo['port']
		else:
			port = 8006
		connected = False
		authenticated = False
		if not connected and not authenticated:
			try:
				if G.hosts[G.current_hostset]['token_name'] and G.hosts[G.current_hostset]['token_value']:
					G.proxmox = proxmoxer.ProxmoxAPI(
						host,
						user=f"{username}@{G.hosts[G.current_hostset]['backend']}",
						token_name=G.hosts[G.current_hostset]['token_name'],
						token_value=G.hosts[G.current_hostset]['token_value'],
						verify_ssl=G.hosts[G.current_hostset]['verify_ssl'], 
						port=port
					)
				elif totp:
					G.proxmox = proxmoxer.ProxmoxAPI(
						host,
						user=f"{username}@{G.hosts[G.current_hostset]['backend']}",
						otp=totp,
						password=passwd,
						verify_ssl=G.hosts[G.current_hostset]['verify_ssl'],
						port=port
					)
				else:
					G.proxmox = proxmoxer.ProxmoxAPI(
						host,
						user=f"{username}@{G.hosts[G.current_hostset]['backend']}",
						password=passwd,
						verify_ssl=G.hosts[G.current_hostset]['verify_ssl'],
						port=port
					)
				connected = True
				authenticated = True
				return connected, authenticated, err
			except proxmoxer.backends.https.AuthenticationError as e:
				err = e
				connected = True
				return connected, authenticated, err
			except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError) as e:
				err = e
				connected = False
	return connected, authenticated, err

def loginwindow():
	if G.hosts[G.current_hostset]['user'] and G.hosts[G.current_hostset]['token_name'] and G.hosts[G.current_hostset]['token_value'] and len(G.hosts) == 1:
		if not reload_connection_settings(show_errors=True):
			return False, False
		popwin = win_popup('Please wait, authenticating...')
		connected, authenticated, error = pveauth(G.hosts[G.current_hostset]['user'])
		popwin.close()
		if not connected:
			win_popup_button(f'Unable to connect to any VDI server, are you connected to the Internet?\nError Info: {error}', 'OK')
			return False, False
		elif connected and not authenticated:
			win_popup_button('Invalid username and/or password, please try again!', 'OK')
			return False, False
		elif connected and authenticated:
			return True, False
	window_data = _build_login_window()
	window = window_data['window']
	result = {'action': None, 'group': None}

	def on_switch(choice):
		if choice and choice != G.current_hostset:
			result['action'] = 'switch'
			result['group'] = choice
			window.destroy()

	def do_cancel():
		result['action'] = 'cancel'
		window.destroy()

	def do_login():
		if window_data['group_combo']:
			G.current_hostset = window_data['group_combo'].get()
		if not reload_connection_settings(show_errors=True):
			win_popup_button('Unable to reload configuration file before login.', 'OK')
			return
		if G.current_hostset not in G.hosts:
			win_popup_button('Selected server group is not defined in the configuration file.', 'OK')
			return
		user = window_data['username_entry'].get()
		passwd = window_data['password_entry'].get()
		totp = None
		if window_data['totp_entry']:
			totp = window_data['totp_entry'].get()
		popwin = win_popup('Please wait, authenticating...')
		connected, authenticated, error = pveauth(user, passwd=passwd, totp=totp)
		popwin.close()
		if not connected:
			win_popup_button(f'Unable to connect to any VDI server, are you connected to the Internet?\nError Info: {error}', 'OK')
		elif connected and not authenticated:
			win_popup_button('Invalid username and/or password, please try again!', 'OK')
		else:
			save_remembered_credentials(
				G.current_hostset,
				user,
				passwd,
				window_data['remember_var'].get()
			)
			result['action'] = 'login'
			window.destroy()

	if window_data['group_combo']:
		window_data['group_combo'].configure(command=on_switch)
	window_data['login_button'].configure(command=do_login)
	if window_data['cancel_button']:
		window_data['cancel_button'].configure(command=do_cancel)
	if window_data['pwreset_button']:
		def open_reset():
			try:
				subprocess.check_call(G.hosts[G.current_hostset]['pwresetcmd'], shell=True)
			except Exception as e:
				win_popup_button(f'Unable to open password reset command.\n\nError Info:\n{e}', 'OK')
		window_data['pwreset_button'].configure(command=open_reset)
	if window_data['settings_button']:
		window_data['settings_button'].configure(command=open_hostpool_settings)
	window.bind('<Return>', lambda event: do_login())
	window.protocol('WM_DELETE_WINDOW', do_cancel)
	if not G.kiosk:
		window.grab_set()
	window.wait_window()

	if result['action'] == 'switch':
		G.current_hostset = result['group']
		return False, True
	if result['action'] == 'login':
		return True, False
	return False, False


def _build_vm_row(parent, vm, on_connect, on_reset):
	frame = ui_frame(parent, border_width=1, border_color=('gray80', 'gray30'))
	frame.pack(fill='x', padx=12, pady=(0, 10))
	info_frame = ui_frame(frame, fg_color='transparent', border_width=0)
	info_frame.pack(side='left', fill='x', expand=True, padx=(0, 8))
	name_label = ctk.CTkLabel(info_frame, text=vm['name'], font=get_font('VM_NAME_FONT'))
	name_label.pack(anchor='w')
	state_label = ctk.CTkLabel(info_frame, text='State: unknown', anchor='w', font=get_font('LABEL_FONT'))
	state_label.pack(anchor='w', pady=(4, 0))
	button_frame = ui_frame(frame, fg_color='transparent', border_width=0)
	button_frame.pack(side='right')
	conn_button = ui_button(button_frame, text='Connect', width=120, command=lambda: on_connect(vm), font=get_font('BUTTON_FONT'))
	conn_button.pack(pady=(0, 4))
	reset_button = None
	if G.show_reset:
		reset_button = ui_button(button_frame, text='Reset', width=120, fg_color='#3b8ed0', hover_color='#4fa1e7', command=lambda: on_reset(vm), font=get_font('BUTTON_FONT'))
		reset_button.pack(pady=(0, 4))
	return frame, state_label, conn_button, reset_button


def showvms():
	vms = getvms()
	if vms == False:
		return False
	if len(vms) < 1:
		win_popup_button('No desktop instances found, please consult with your system administrator', 'OK')
		return False
	root = get_hidden_root()
	window = VDIWindow(root)
	window.title(G.title)
	set_window_icon(window)
	_, screen_h = get_screen_dimensions()
	panel = prepare_vdi_window(
		window,
		fullscreen=True,
		panel_width=900,
		panel_height=screen_h - 48
	)
	container = ui_frame(panel, fg_color='transparent', border_width=0)
	container.pack(padx=20, pady=20, fill='both', expand=True)
	if G.imagefile:
		image = load_image(G.imagefile, for_ctk_label=True)
		if image:
			logo = ctk.CTkLabel(container, image=image, text='', fg_color='transparent')
			logo.image = image
			logo.pack(pady=(0, 12))
	ctk.CTkLabel(container, text=G.title, font=get_font('TITLE_FONT')).pack(pady=(0, 6))
	ctk.CTkLabel(container, text='Please select a desktop instance to connect to', font=get_font('LABEL_FONT')).pack(pady=(0, 14))
	ui_frame(container, height=1, fg_color=('gray70', 'gray30'), border_width=0).pack(fill='x', padx=10, pady=(0, 14))
	current_page = 0
	visible_vms = [vm for vm in vms if vm.get('status') != 'unknown']
	total_pages = max(1, math.ceil(len(visible_vms) / G.page_size))
	vm_frame = ui_frame(container, fg_color='transparent', border_width=0)
	vm_frame.pack(fill='both', expand=True)
	page_frame = ui_frame(container, fg_color='transparent', border_width=0)
	page_separator = ui_frame(container, height=1, fg_color=('gray70', 'gray30'), border_width=0)
	page_label = ctk.CTkLabel(page_frame, text=f'Page {current_page + 1} of {total_pages}', font=get_font('LABEL_FONT'))
	prev_button = ui_button(page_frame, text='Previous', width=100, font=get_font('BUTTON_FONT'))
	next_button = ui_button(page_frame, text='Next', width=100, font=get_font('BUTTON_FONT'))
	# These are initially packed in build_vm_list based on total_pages
	vm_controls = {}
	current_vmlist = getvms(listonly=True)

	def update_vm_row(vm, state_label, conn_button):
		state = 'stopped'
		if vm.get('status') == 'running':
			if 'lock' in vm:
				state = vm['lock']
				if state in ('suspending', 'suspended'):
					if state == 'suspended':
						state = 'starting'
				conn_button.configure(state='disabled')
			else:
				state = vm['status']
				conn_button.configure(state='normal')
		else:
			conn_button.configure(state='normal')
		state_label.configure(text=f'State: {state}')

	def on_connect(vm):
		vmaction(vm['node'], vm['vmid'], vm['type'])

	def on_reset(vm):
		vmaction(vm['node'], vm['vmid'], vm['type'], action='reload')

	def update_page_controls():
		page_label.configure(text=f'Page {current_page + 1} of {total_pages}')
		prev_button.configure(state='normal' if current_page > 0 else 'disabled')
		next_button.configure(state='normal' if current_page < total_pages - 1 else 'disabled')

	def change_page(delta):
		nonlocal current_page
		current_page = max(0, min(total_pages - 1, current_page + delta))
		update_page_controls()
		build_vm_list(visible_vms)

	prev_button.configure(command=lambda: change_page(-1))
	next_button.configure(command=lambda: change_page(1))

	def build_vm_list(vms_to_render):
		filtered_vms = [vm for vm in vms_to_render if vm.get('status') != 'unknown']
		nonlocal total_pages, current_page
		total_pages = max(1, math.ceil(len(filtered_vms) / G.page_size))
		current_page = min(current_page, total_pages - 1)
		start = current_page * G.page_size
		end = start + G.page_size
		page_items = filtered_vms[start:end]
		for child in vm_frame.winfo_children():
			child.destroy()
		vm_controls.clear()
		for i, vm in enumerate(page_items):
			frame, state_label, conn_button, reset_button = _build_vm_row(vm_frame, vm, on_connect, on_reset)
			update_vm_row(vm, state_label, conn_button)
			vm_controls[str(vm['vmid'])] = {
				'state': state_label,
				'button': conn_button
			}
			if i < len(page_items) - 1:
				ui_frame(vm_frame, height=1, fg_color=('gray75', 'gray25'), border_width=0).pack(fill='x', padx=24, pady=(0, 10))
		update_page_controls()
		# Control visibility of pagination elements
		if total_pages > 1:
			page_separator.pack(side='bottom', fill='x', pady=(10, 0))
			page_frame.pack(side='bottom', fill='x', pady=(4, 0))
			page_label.pack(side='left')
			prev_button.pack(side='left', padx=(10, 8))
			next_button.pack(side='left')
		else:
			page_separator.pack_forget()
			page_frame.pack_forget()



	refresh_id = None
	timeout_id = None

	def refresh():
		nonlocal current_vmlist, refresh_id
		
		# First, try to get the list of VMs (vmid, name, node) for structural comparison
		new_list_only_vms = getvms(listonly=True)
		if new_list_only_vms is False:
			# If there was an error getting the list (e.g., timeout),
			# just reschedule and return without updating the UI.
			if window.winfo_exists():
				refresh_id = window.after(5000, refresh)
			return

		# If the list structure has changed, or if it's the first refresh
		if new_list_only_vms != current_vmlist:
			current_vmlist = new_list_only_vms.copy()
			# Get full VM details to rebuild the list
			new_vms_full_details = getvms()
			if new_vms_full_details is False:
				if window.winfo_exists():
					refresh_id = window.after(5000, refresh)
				return
			if new_vms_full_details:
				build_vm_list(new_vms_full_details)
		else:
			# If only VM statuses might have changed, get full details and update existing rows
			new_vms_full_details = getvms()
			if new_vms_full_details is False:
				if window.winfo_exists():
					refresh_id = window.after(5000, refresh)
				return
			if new_vms_full_details:
				for vm in new_vms_full_details:
					row = vm_controls.get(str(vm['vmid']))
					if row: # Only update if the row exists
						update_vm_row(vm, row['state'], row['button'])
		if window.winfo_exists():
			refresh_id = window.after(5000, refresh)

	def reset_timeout(event=None):
		nonlocal timeout_id
		if timeout_id:
			window.after_cancel(timeout_id)
		if G.timeout > 0:
			timeout_id = window.after(G.timeout * 60 * 1000, close_vm_window)

	def close_vm_window():
		nonlocal refresh_id, timeout_id
		if refresh_id and window.winfo_exists():
			window.after_cancel(refresh_id)
		if timeout_id and window.winfo_exists():
			window.after_cancel(timeout_id)
		result.update({'logout': True})
		window.destroy()

	ui_button(container, text='Logout', fg_color='#d65f5f', hover_color='#d85f5f', command=close_vm_window, font=get_font('BUTTON_FONT')).pack(
		side='bottom', pady=(12, 0), fill='x'
	)
	build_vm_list(vms)
	result = {'logout': False}
	window.protocol('WM_DELETE_WINDOW', close_vm_window)
	window.deiconify()
	apply_kiosk_state(window)
	if G.timeout > 0:
		reset_timeout()
		window.bind_all("<Any-KeyPress>", reset_timeout)
		window.bind_all("<Button>", reset_timeout)
		window.bind_all("<Motion>", reset_timeout)
	refresh_id = window.after(5000, refresh)
	window.wait_window()
	return not result['logout']

def main():
	G.scaling = 1 # TKinter requires integers
	parser = argparse.ArgumentParser(description='Proxmox VDI Client')
	parser.add_argument('--list_themes', help='List all available themes', action='store_true')
	parser.add_argument('--config_type', help='Select config type (default: file)', choices=['file', 'http'], default='file')
	parser.add_argument('--config_location', help='Specify the config location (default: search for config file)', default=None)
	parser.add_argument('--config_username', help="HTTP basic authentication username (default: None)", default=None)
	parser.add_argument('--config_password', help="HTTP basic authentication password (default: None)", default=None)
	parser.add_argument('--ignore_ssl', help="HTTPS ignore SSL certificate errors (default: False)", action='store_false', default=True)
	parser.add_argument('--settings', help='Open VDI settings editor before login', action='store_true')
	args = parser.parse_args()
	if args.list_themes:
		print('appearance modes: System, Light, Dark')
		print('default color theme: blue')
		return
	setcmd()
	if not loadconfig(config_location=args.config_location, config_type=args.config_type, config_username=args.config_username, config_password=args.config_password, ssl_verify=args.ignore_ssl):
		return False
	apply_theme()
	if args.settings:
		open_hostpool_settings()
	loggedin = False
	switching = False
	while True:
		if not loggedin:
			loggedin, switching = loginwindow()
			if not loggedin and not switching:
				if G.hosts[G.current_hostset]['user'] and G.hosts[G.current_hostset]['token_name'] and G.hosts[G.current_hostset]['token_value']: # This means if we don't exit we'll be in an infinite loop
					return 1
				break
			elif not loggedin and switching:
				pass
			else:
				if G.hosts[G.current_hostset]['auto_vmid']:
					vms = getvms()
					for row in vms:
						if row['vmid'] == G.hosts[G.current_hostset]['auto_vmid']:
							vmaction(row['node'], row['vmid'], row['type'], action='connect')
							return 0
					win_popup_button(f"No VDI instance with ID {G.hosts[G.current_hostset]['auto_vmid']} found!", 'OK')
				vmstat = showvms()
				if not vmstat:
					G.proxmox = None
					loggedin = False
					if G.hosts[G.current_hostset]['user'] and G.hosts[G.current_hostset]['token_name'] and G.hosts[G.current_hostset]['token_value'] and len(G.hosts) == 1: # This means if we don't exit we'll be in an infinite loop
						return 0
				else:
					return

if __name__ == '__main__':
	sys.exit(main())
