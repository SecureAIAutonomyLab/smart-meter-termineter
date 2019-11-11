#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  c1218/connection.py
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are
#  met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following disclaimer
#    in the documentation and/or other materials provided with the
#    distribution.
#  * Neither the name of the project nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
#  A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
#  OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
#  SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
#  LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
#  DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
#  THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

from __future__ import unicode_literals

import logging
import random
import sys
import time

from c1218.data import *
from c1218.errors import C1218NegotiateError, C1218IOError, C1218ReadTableError, C1218WriteTableError
from c1218.utilities import check_data_checksum, packet_checksum
from c1219.data import C1219ProcedureInit
from c1219.errors import C1219ProcedureError

import serial

import pdb
'''
hasattr() Parameters:
The hasattr(object, name) method takes two parameters:
+ object - object whose named attribute is to be checked.
+ name - name of the attribute to be searched.
'''
if hasattr(serial, 'protocol_handler_packages') and not 'c1218.urlhandler' in serial.protocol_handler_packages:
	serial.protocol_handler_packages.append('c1218.urlhandler')

if hasattr(logging, 'NullHandler'):
	logging.getLogger('c1218').addHandler(logging.NullHandler())

class ConnectionBase(object):
	def __init__(self, device, c1218_settings={}, serial_settings=None, toggle_control=True, **kwargs):
		"""
		This is a C12.18 driver for serial connections.  It relies on PySerial
		to communicate with an ANSI Type-2 Optical probe to communicate
		with a device (presumably a smart meter).

		:param str device: A connection string to be passed to the PySerial
		  library.  If PySerial is new enough, the serial_for_url function
		  will be used to allow the user to use a rfc2217 bridge.
		:param dict c1218_settings: A settings dictionary to configure the C1218
		  parameters of 'nbrpkts' and 'pktsize'  If not provided the default
		  settings of 2 (nbrpkts) and 512 (pktsize) will be used.
		:param dict serial_settings: A PySerial settings dictionary to be applied to
		  the serial connection instance.
		:param bool toggle_control: Enables or diables automatically settings
		  the toggle bit in C12.18 frames.
		"""
		#print("connection.py - 79 - class Conenctionbase(object)")
		self.logger = logging.getLogger('c1218.connection')
		self.loggerio = logging.getLogger('c1218.connection.io')
		#Fifth Parameter
		self.toggle_control = toggle_control
		self._toggle_bit = False
		if hasattr(serial, 'serial_for_url'):
			self.serial_h = serial.serial_for_url(device)
		else:
			self.logger.warning('serial library does not have serial_for_url functionality, it\'s not the latest version')
			self.serial_h = serial.Serial(device)
		self.logger.debug('successfully opened serial device: ' + device)
		# Second Parameter
		self.device = device
		# Third Parameter (pktsize and nbrpkts)
		#self.c1218_pktsize = (c1218_settings.get('pktsize') or 512) # Original
		#self.c1218_nbrpkts = (c1218_settings.get('nbrpkts') or 2) #Original
		self.c1218_pktsize = (c1218_settings.get('pktsize') or 512)
		self.c1218_nbrpkts = (c1218_settings.get('nbrpkts') or 2)
		# Fourth Parameter
		if serial_settings:
			self.logger.debug('applying pySerial settings dictionary')
			self.serial_h.parity = serial_settings['parity']
			self.serial_h.baudrate = serial_settings['baudrate']
			self.serial_h.bytesize = serial_settings['bytesize']
			self.serial_h.xonxoff = serial_settings['xonxoff']
			self.serial_h.interCharTimeout = serial_settings['interCharTimeout']
			self.serial_h.rtscts = serial_settings['rtscts']
			self.serial_h.timeout = serial_settings['timeout']
			self.serial_h.stopbits = serial_settings['stopbits']
			self.serial_h.dsrdtr = serial_settings['dsrdtr']
			self.serial_h.writeTimeout = serial_settings['writeTimeout']

		try:
			self.serial_h.setRTS(True)
		except IOError:
			self.logger.warning('could not set RTS to True')
		else:
			self.logger.debug('set RTS to True')
		try:
			self.serial_h.setDTR(False)
		except IOError:
			self.logger.warning('could not set DTR to False')
		else:
			self.logger.debug('set DTR to False')

		self.logged_in = False
		self._initialized = False
		self.c1219_endian = '<'

	def __repr__(self):
		return '<' + self.__class__.__name__ + ' Device: ' + self.device + ' >'

	def send(self, data):
		"""
		This sends a raw C12.18 frame and waits checks for an ACK response.
		In the event that a NACK is received, this function will attempt
		to resend the frame up to 3 times.

		:param data: the data to be transmitted
		:type data: str, :py:class:`~c1218.data.C1218Packet`
		"""
		if not isinstance(data, C1218Packet):
			data = C1218Packet(data)
		if self.toggle_control:  # bit wise
			if self._toggle_bit:
				data.set_control(ord(data.control) | 0x20)
				self._toggle_bit = False
			elif not self._toggle_bit:
				if ord(data.control) & 0x20:
					data.set_control(ord(data.control) ^ 0x20)
				self._toggle_bit = True
		elif self.toggle_control and not isinstance(data, C1218Packet):
			self.loggerio.warning('toggle bit is on but the data is not a C1218Packet instance')
		data = data.build()
		self.loggerio.debug("sending frame,  length: {0:<3} data: {1}".format(len(data), binascii.b2a_hex(data).decode('utf-8')))
		for pktcount in range(0, 3):
			self.write(data)
			response = self.serial_h.read(1)
			if response == NACK:
				self.loggerio.warning('received a NACK after writing data')
				time.sleep(0.10)
			elif len(response) == 0:
				self.loggerio.error('received empty response after writing data')
				time.sleep(0.10)
			elif response != ACK:
				self.loggerio.error('received unknown response: ' + hex(ord(response)) + ' after writing data')
			else:
				return
		self.loggerio.critical('failed 3 times to correctly send a frame')
		raise C1218IOError('failed 3 times to correctly send a frame')

	def recv(self, full_frame=False):
		"""
		Receive a C1218Packet, the payload data is returned.

		:param bool full_frame: If set to True, the entire C1218 frame is
		  returned instead of just the payload.
		"""
		payloadbuffer = b''
		tries = 3
		while tries:
			tmpbuffer = self.serial_h.read(1)
			#Debug Code - checked
			#print("connection.py - 182: {} and {}".format("tmpbuffer.read(1):",tmpbuffer))

			if tmpbuffer != b'\xee':
				#Debug Code - skipped
				#print("Inside tmpbuffer != b... ")
				self.loggerio.error('did not receive \\xee as the first byte of the frame')
				self.loggerio.debug('received \\x' + binascii.b2a_hex(tmpbuffer).decode('utf-8') + ' instead')
				tries -= 1
				continue
			tmpbuffer += self.serial_h.read(5)
			#Debug code
			#print("{} and {}".format("tmpbuffer += self.serial_h.read(5)",tmpbuffer))
			sequence, length = struct.unpack('>xxxBH', tmpbuffer)
			#Debug code
			#print("sequence: {} and Length {}".format(sequence,length))
			payload = self.serial_h.read(length)
			#Debug code
			#print("connection.py - 199: Payload: {}".format(payload))
			tmpbuffer += payload
			#Debug code
			#print("196: tmpbuffer: {}".format(tmpbuffer))
			chksum = self.serial_h.read(2)
			#Debug code
			#print("chksum: {}".format(chksum))
			if chksum == packet_checksum(tmpbuffer):
				#Debug code
				#print("chksum: {} and packet_checksum(tmpbuffer): {}".format(chksum,packet_checksum(tmpbuffer)))
				#Debug code
				#print("ACK: {}".format(ACK))
				self.serial_h.write(ACK)
				#Debug code
				#print("tmpbuffer: {} and chksum {}".format(tmpbuffer,chksum))
				data = tmpbuffer + chksum
				#Debug code
				#print("210: data: {}".format(data))
				self.loggerio.debug("received frame, length: {0:<3} data: {1}".format(len(data), binascii.b2a_hex(data).decode('utf-8')))
				payloadbuffer += payload
				#Debug code
				#print("conection.py- 218 payloadbuffer: {}".format(payloadbuffer))
				if sequence == 0:
					#Debug code
					#print("217: sequence {} and full_frame {}".format(sequence,full_frame))
					if full_frame:
						#Debug code
						#print("220: Inside <if> full_frame: {}".format(full_frame))
						payloadbuffer = data
					#Debug code
					#print("223: sys.version_info[0]: {}".format(sys.version_info[0]))
					if sys.version_info[0] == 2:
						#Debug code
						#print("226: Inside <if> sys.version_info[0]: {}".format(sys.version_info[0]))
						payloadbuffer = bytearray(payloadbuffer)
					#Debug code
					#print("229: Before return payloadbuffer: {}".format(payloadbuffer))
					return payloadbuffer
					#Debug code
					#print("232:After return payloadbuffer: {}".format(payloadbuffer))
				else:
					#Debug Code
					#rint("Inside Else")
					tries = 3
			else:
				#Debug Code
				#print("Inside Else")
				self.serial_h.write(NACK)
				self.loggerio.warning('crc does not match on received frame')
				tries -= 1
		#Debug Code
		#print("Does it fail?")
		self.loggerio.critical('failed 3 times to correctly receive a frame')
		raise C1218IOError('failed 3 times to correctly receive a frame')

	#Modified function recv2
	def recv2(self, full_frame=False):
		"""
		Receive a C1218Packet, the payload data is returned.

		:param bool full_frame: If set to True, the entire C1218 frame is
		  returned instead of just the payload.
		"""
		payloadbuffer = b''
		tries = 3
		while tries:
			tmpbuffer = self.serial_h.read(1)
			#Debug Code - checked
			#print("connection.py - 100: {} and {}".format("tmpbuffer.read(1):",tmpbuffer))

			if tmpbuffer != b'\xee':
				#Debug Code - skipped
				#print("Inside tmpbuffer != b... ")
				self.loggerio.error('did not receive \\xee as the first byte of the frame')
				self.loggerio.debug('received \\x' + binascii.b2a_hex(tmpbuffer).decode('utf-8') + ' instead')
				tries -= 1
				continue
			tmpbuffer += b'\xee'
			tmpbuffer += self.serial_h.read(4)
			#Debug code
			#print("{} and {}".format("tmpbuffer += self.serial_h.read(4)",tmpbuffer))
			sequence, length = struct.unpack('>xxxBH', tmpbuffer)
			#Debug code
			#print("conenction.py - 282: sequence: {} and Length {}".format(sequence,length))
			payload = self.serial_h.read(length)
			#Debug code
			#print("connection.py - 199: Payload: {}".format(payload))
			tmpbuffer += payload
			#Debug code
			#print("196: tmpbuffer: {}".format(tmpbuffer))
			chksum = self.serial_h.read(2)
			#Debug code
			#print("chksum: {}".format(chksum))
			if chksum == packet_checksum(tmpbuffer):
				#Debug code
				#print("chksum: {} and packet_checksum(tmpbuffer): {}".format(chksum,packet_checksum(tmpbuffer)))
				#Debug code
				#print("ACK: {}".format(ACK))
				self.serial_h.write(ACK)
				#Debug code
				#print("tmpbuffer: {} and chksum {}".format(tmpbuffer,chksum))
				data = tmpbuffer + chksum
				#Debug code
				#print("210: data: {}".format(data))
				self.loggerio.debug("received frame, length: {0:<3} data: {1}".format(len(data), binascii.b2a_hex(data).decode('utf-8')))
				payloadbuffer += payload
				#Debug code
				#print("connection.py- 218 payloadbuffer: {}".format(payloadbuffer))
				if sequence == 0:
					#Debug code
					#print("217: sequence {} and full_frame {}".format(sequence,full_frame))
					if full_frame:
						#Debug code
						#print("220: Inside <if> full_frame: {}".format(full_frame))
						payloadbuffer = data
					#Debug code
					#print("223: sys.version_info[0]: {}".format(sys.version_info[0]))
					if sys.version_info[0] == 2:
						#Debug code
						#print("226: Inside <if> sys.version_info[0]: {}".format(sys.version_info[0]))
						payloadbuffer = bytearray(payloadbuffer)
					#Debug code
					#print("229: Before return payloadbuffer: {}".format(payloadbuffer))
					return payloadbuffer
					#Debug code
					#print("232:After return payloadbuffer: {}".format(payloadbuffer))
				else:
					#Debug Code
					#print("Inside Else")
					tries = 3
			else:
				#Debug Code
				#print("Inside Else")
				self.serial_h.write(NACK)
				self.loggerio.warning('crc does not match on received frame')
				tries -= 1
		#Debug Code
		#print("Does it fail?")
		self.loggerio.critical('failed 3 times to correctly receive a frame')
		raise C1218IOError('failed 3 times to correctly receive a frame')

	def write(self, data):
		"""
		Write raw data to the serial connection. The CRC must already be
		included at the end. This function is not meant to be called
		directly.

		:param str data: The raw data to write to the serial connection.
		"""
		return self.serial_h.write(data)

	def read(self, size):
		"""
		Read raw data from the serial connection. This function is not
		meant to be called directly.

		:param int size: The number of bytes to read from the serial connection.
		"""
		data = self.serial_h.read(size)
		self.logger.debug('read data, length: ' + str(len(data)) + ' data: ' + binascii.b2a_hex(data).decode('utf-8'))
		self.serial_h.write(ACK)
		if sys.version_info[0] == 2:
			data = bytearray(data)
		return data

	def close(self):
		"""
		Send a terminate request and then disconnect from the serial device.
		"""
		if self._initialized:
			self.stop()
		self.logged_in = False
		return self.serial_h.close()

class Connection(ConnectionBase):
	def __init__(self, *args, **kwargs):
		"""
		This is a C12.18 driver for serial connections.  It relies on PySerial
		to communicate with an ANSI Type-2 Optical probe to communicate
		with a device (presumably a smart meter).

		:param str device: A connection string to be passed to the PySerial
		  library.  If PySerial is new enough, the serial_for_url function
		  will be used to allow the user to use a rfc2217 bridge.
		:param dict c1218_settings: A settings dictionary to configure the C1218
		  parameters of 'nbrpkts' and 'pktsize'  If not provided the default
		  settings of 2 (nbrpkts) and 512 (pktsize) will be used.
		:param dict serial_settings: A PySerial settings dictionary to be applied to
		  the serial connection instance.
		:param bool toggle_control: Enables or disables automatically settings
		  the toggle bit in C12.18 frames.
		:param bool enable_cache: Cache specific, read only tables in memory,
		  the first time the table is read it will be stored for retreival
		  on subsequent requests.  This is enabled only for specific tables
		  (currently only 0 and 1).
		"""
		#print("conneciton.py - 395 - Class Connection(ConnectionBase)")
		enable_cache = kwargs.pop('enable_cache', True)
		super(Connection, self).__init__(*args, **kwargs)
		self.caching_enabled = enable_cache
		self._cacheable_tables = [0, 1]
		self._table_cache = {}
		if enable_cache:
			self.logger.info('selective table caching has been enabled')

	def flush_table_cache(self):
		self.logger.info('flushing all cached tables')
		self._table_cache = {}

	def set_table_cache_policy(self, cache_policy):
		if self.caching_enabled == cache_policy:
			return
		self.caching_enabled = cache_policy
		if cache_policy:
			self.logger.info('selective table caching has been enabled')
		else:
			self.flush_table_cache()
			self.logger.info('selective table caching has been disabled')
		return

	def start(self):
		"""
		Send an identity request and then a negotiation request.
		"""
		#pdb.set_trace()
		#print("connection.py - 423 - inside start")
		self.serial_h.flushOutput()
		self.serial_h.flushInput()
		#print("\n\n\n\n\n******************Start of Indent******************\n\n\n\n\n\n")
		self.send(C1218IdentRequest())
		#Debug code
		#print("Before data = self.recv()")
		
		# C12.18: 
		# 4.2.2.2: Responses/ page 6
		# 4.2.2.3: Identification Service
		data = self.recv()
		
		#Debug code
		#print("connection.py - 437: data: {}".format(data))
		#print("************data: {}".format(data))
		if data[0] != 0x00:

			self.logger.error('received incorrect response to identification service request')
			return False

		#print("\n\n\n\n\n******************End of Indent******************\n\n\n\n\n\n")

		#Modify: Comment the whole block line below

		#print("\n\n\n\n\n******************Start of Negotation******************\n\n\n\n\n\n")
		
		self._initialized = True
		self.send(C1218NegotiateRequest(self.c1218_pktsize, self.c1218_nbrpkts, baudrate=9600))
		#Debug code
		#print("346: Before data = self.recv()")
		data = self.recv()
		#Debug code
		#print("349: After data = self.recv(): {}".format(data))
		#print("350: After data = self.recv(): data[0] {}".format(data[0]))
		#data[0] = 1 != 0x00:
		#Original: if data[0] != 0x00:
		if data[0] != 0x00:
			#print("connection.py - 369 - Inside Received Incoonect Response")
			self.logger.error('received incorrect response to negotiate service request')
			self.stop()
			#Original Code: raise C1218NegotiateError('Heyyy received incorrect response to negotiate service request', data[0])
			raise C1218NegotiateError('Heyyy received incorrect response to negotiate service request', data[0])

		'''
		Starting Modify
		'''
		#print("\n\n\n\n\n******************End of Negotiation******************\n\n\n\n\n\n")
		#print("connection.py - 376 Starting Modifying code")
		#self.send(C1218IdentRequest())
		#Debug code
		#print("Before data = self.recv()")
		
		# C12.18: 
		# 4.2.2.2: Responses/ page 6
		# 4.2.2.3: Identification Service
		#data = self.recv() Orginal
		#New code :data = self.recv2()
		#data = self.recv2()
		#print("connection.py-388 - Ending Modifying Code")
		'''
		Ending Modify
		'''
		
		return True

	def stop(self, force=False):
		"""
		Send a terminate request.

		:param bool force: ignore the remote devices response
		"""
		if self._initialized:
			self.send(C1218TerminateRequest())
			data = self.recv()
			if data == b'\x00' or force:
				self._initialized = False
				self._toggle_bit = False
				return True
		return False

	def login(self, username='0000', userid=0, password=None):
		#print("connection.py - 391: username: {}".format(username))
		#print("connection.py - 392: Username Type: {}".format(type(username)))
		#print("\n\n\n\n\n******************Start of Login******************\n\n\n\n\n\n")
		"""
		Log into the connected device.

		:param str username: the username to log in with (len(username) <= 10)
		:param int userid: the userid to log in with (0x0000 <= userid <= 0xffff)
		:param str password: password to log in with (len(password) <= 20)
		:rtype: bool
		"""
		#print("connection.py-516 - login")
		if password and len(password) > 20:
			self.logger.error('password longer than 20 characters received')
			raise Exception('password longer than 20 characters, login failed')
	
		#for userid in range(0,65000):
		#print("connection.py - 522: Username{} and userid:{}".format(username, userid))
		self.send(C1218LogonRequest(username, userid))
		data = self.recv()
		#print("connection.py- 524: {}".format(data))
			#print("Connection.py - 405:{} and Data: {}".format(userid,data))

		if data != b'\x00':
			self.logger.warning('login failed, username and user id rejected')
			return False
		#print("connection.py - 530 - User ID: {}".format(userid))
		#print("connection.py - 410 Data: {}".format(data))
				#break
		#print("Got the User ID: {}".format(userid))
		#print("\n\n\n\n\n******************End of Login Username******************\n\n\n\n\n\n")


		if password is not None:
			self.send(C1218SecurityRequest(password))
			data = self.recv()

			if data != b'\x00':
				self.logger.warning('login failed, password rejected')
				
				return False

		self.logged_in = True
		#print("\n\n\n\n\n******************End of Login True******************\n\n\n\n\n\n")
		return True


	def logoff(self):
		"""
		Send a logoff request.

		:rtype: bool
		"""
		self.send(C1218LogoffRequest())
		data = self.recv()
		if data == b'\x00':
			self._initialized = False
			return True
		return False

	def get_table_data(self, tableid, octetcount=None, offset=None):
		#print("\n\n\n***connection.py-564 : Inside get table data****\n\n\n")
		"""
		Read data from a table. If successful, all of the data from the
		requested table will be returned.

		:param int tableid: The table number to read from (0x0000 <= tableid <= 0xffff)
		:param int octetcount: Limit the amount of data read, only works if
		  the meter supports this type of reading.
		:param int offset: The offset at which to start to read the data from.
		"""
		#print("\n\n\n***Table ID: {}****\n\n\n".format(tableid))
		if self.caching_enabled and tableid in self._cacheable_tables and tableid in self._table_cache.keys():
			self.logger.info('returning cached table #' + str(tableid))
			return self._table_cache[tableid]
		self.send(C1218ReadRequest(tableid, offset, octetcount))
		data = self.recv()
		status = data[0]
		if status != 0x00:
			status = status
			details = (C1218_RESPONSE_CODES.get(status) or 'unknown response code')
			self.logger.error('could not read table id: ' + str(tableid) + ', error: ' + details)
			raise C1218ReadTableError('could not read table id: ' + str(tableid) + ', error: ' + details, status)
		if len(data) < 4:
			if len(data) == 0:
				self.logger.error('could not read table id: ' + str(tableid) + ', error: no data was returned')
				raise C1218ReadTableError('could not read table id: ' + str(tableid) + ', error: no data was returned')
			self.logger.error('could not read table id: ' + str(tableid) + ', error: data read was corrupt, invalid length (less than 4)')
			raise C1218ReadTableError('could not read table id: ' + str(tableid) + ', error: data read was corrupt, invalid length (less than 4)')
		length = struct.unpack('>H', data[1:3])[0]
		chksum = data[-1]
		data = data[3:-1]
		if len(data) != length:
			self.logger.error('could not read table id: ' + str(tableid) + ', error: data read was corrupt, invalid length')
			raise C1218ReadTableError('could not read table id: ' + str(tableid) + ', error: data read was corrupt, invalid length')
		if not check_data_checksum(data, chksum):
			self.logger.error('could not read table id: ' + str(tableid) + ', error: data read was corrupt, invalid check sum')
			raise C1218ReadTableError('could not read table id: ' + str(tableid) + ', error: data read was corrupt, invalid checksum')

		if self.caching_enabled and tableid in self._cacheable_tables and not tableid in self._table_cache.keys():
			self.logger.info('caching table #' + str(tableid))
			self._table_cache[tableid] = data
		#print("\n\n\n***End of get_table_data****\n\n\n")	
		return data

	def set_table_data(self, tableid, data, offset=None):
		"""
		Write data to a table.

		:param int tableid: The table number to write to (0x0000 <= tableid <= 0xffff)
		:param str data: The data to write into the table.
		:param int offset: The offset at which to start to write the data (0x000000 <= octetcount <= 0xffffff).
		"""
		#print("\n\n\n****Table ID: {}\n\n\n".format(tableid))
		#print("\n\n\n****Data: {}\n\n\n".format(data))
		self.send(C1218WriteRequest(tableid, data, offset))
		data = self.recv()
		if data[0] != 0x00:
			status = data[0]
			details = (C1218_RESPONSE_CODES.get(status) or 'unknown response code')
			self.logger.error('could not write data to the table, error: ' + details)
			raise C1218WriteTableError('could not write data to the table, error: ' + details, status)
		return

	def run_procedure(self, process_number, std_vs_mfg, params=''):
		"""
		Initiate a C1219 procedure, the request is written to table 7 and
		the response is read from table 8.

		:param int process_number: The numeric procedure identifier (0 <= process_number <= 2047).
		:param bool std_vs_mfg: Whether the procedure is manufacturer specified
		  or not. True is manufacturer specified. Standard or manufacture.
		:param bytes params: The parameters to pass to the procedure initiation request.
		:return: A tuple of the result code and the response data.
		:rtype: tuple
		"""
		seqnum = random.randint(2, 254)
		self.logger.info('starting procedure: ' + str(process_number) + ' (' + hex(process_number) + ') sequence number: ' + str(seqnum) + ' (' + hex(seqnum) + ')')
		procedure_request = C1219ProcedureInit(self.c1219_endian, process_number, std_vs_mfg, 0, seqnum, params).build()
		#print("\n\n\n***connection.py - 643: Before Set Table Data****\n\n\n")
		self.set_table_data(7, procedure_request)
		#print("\n\n\n***connection.py - 645: Before Get Table Data****\n\n\n")
		response = self.get_table_data(8)
		#print("Responses: {}".format(response))
		#print("\n\n\n***After Get Table Data****\n\n\n")
		if response[:3] == procedure_request[:3]:
			return response[3], response[4:]
		else:
			self.logger.error('invalid response from procedure response table (table #8)')
			raise C1219ProcedureError('invalid response from procedure response table (table #8)')
