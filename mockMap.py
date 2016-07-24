#!/usr/bin/env python3

import argparse
import threading
import configparser
import telnetlib
import ipaddress
import sys
# TODO let app use logging module
import logging
from socket import timeout
from timeit import default_timer
from datetime import datetime
from geopy.geocoders import Nominatim
from geographiclib.geodesic import Geodesic
from flask import Flask, render_template, request, jsonify

# use config file for credentials and setup
config = configparser.ConfigParser()
config.read('config.conf')
args = None

app = Flask(__name__)
loc = None
loc_target = None
speed = None
rate = None
# TODO replace with args
last_update = None
telnet = []

def check_args():
    parser = argparse.ArgumentParser(description='Movement simulator for mocking locations.')
    parser.add_argument('location', help='a Google Maps readable location')
    parser.add_argument('ip', nargs='+', type=ipaddress.ip_address, help='IP addresses for telnet connection')
    parser.add_argument('-s', metavar='speed', type=float, default=10, help='initial movement speed')
    parser.add_argument('-r', metavar='rate', type=int, default=1, help='GPS refresh rate in seconds')
    parser.add_argument('-v', action='store_true', help='verbose messages')
    args = parser.parse_args()
    return args

def log(message, log_type):
    if log_type == '*' and not args.v:
        pass
    else:
        print('[' + log_type + '] ' + message)

def get_location(loc):
    geolocator = Nominatim()
    location = geolocator.geocode(loc)
    return location

def kmh_to_ms(kmh):
    return kmh/3.6

def renew_position():
    global loc, speed, last_update
    geod = Geodesic.WGS84
    path = geod.InverseLine(loc[0], loc[1], loc_target[0], loc_target[1])
    log('Thread started at {time}, distance to move is {distance:0.3f}m'
            .format(time=datetime.now(), distance=path.s13), '*')

    # set last_update for first run assuming optimal
    if last_update is None:
        last_update = default_timer() - rate
    time_now = default_timer()
    step_distance = kmh_to_ms(speed) * (time_now - last_update)
    last_update = time_now

    # avoid float precision problems with small distance
    if path.s13 > 0.001:
        move = min(step_distance, path.s13)
        loc_new = path.Position(move, Geodesic.STANDARD | Geodesic.LONG_UNROLL)
        loc = (loc_new['lat2'], loc_new['lon2'])

        log('Moved {:.3f}m to {:.5f}, {:.5f}'
                .format(loc_new['s12'], loc_new['lat2'], loc_new['lon2']), '*')
    else:
        log('No movement', '*')

    # send position to telnet hosts
    for i, connection in enumerate(telnet):
        try:
            connection.write('geo fix {longitude} {latitude}\n'.format(longitude=loc[1], latitude=loc[0]).encode('ascii'))
            log('Position sent to {ip}'.format(ip=args.ip[i]), '*')
        except (ConnectionRefusedError, BrokenPipeError, IOError):
            log('Connection lost to {ip} reconnecting...'.format(ip=args.ip[i]), '-')
            connection.close()

            while True:
                try:
                    connection.open(str(args.ip[i]), config.getint('telnet', 'port'), config.getint('telnet', 'timeout'))
                except IOError:
                    pass
                else:
                    break

    threading.Timer(rate, renew_position).start()

@app.route('/')
def http_worldmap():
    return render_template('map.html', key=config['google']['mapsKey'], loc=loc, loc_target=loc_target)

@app.route('/location', methods=['GET', 'POST'])
def http_location():
    global loc_target
    if request.method == 'GET':
        return jsonify(latitude=loc[0], longitude=loc[1])
    if request.method == 'POST':
        loc_target = (float(request.form['latitude']), float(request.form['longitude']))
        log('New target set to ({latitude}, {longitude})'
                .format(latitude=request.form['latitude'], longitude=request.form['longitude']), '+')
        return 'ok'

def main():
    global args, loc, loc_target, speed, rate, verbose, telnet

    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    # set globals
    args = check_args()
    location = get_location(args.location)
    loc = (location.latitude, location.longitude)
    loc_target = (location.latitude, location.longitude)
    speed = args.s
    rate = args.r

    for host in args.ip:
        try:
            telnet.append(telnetlib.Telnet(str(host), config.getint('telnet', 'port'), config.getint('telnet', 'timeout')))
        except timeout:
            log('Could not connect to telnet on {ip} - timed out (wrong IP?)'.format(ip=host), '-')
            sys.exit(1)
        except ConnectionRefusedError:
            log('Connection to {ip} was refused. Is telnet running and port open?'.format(ip=host), '-')
            sys.exit(1)
        else:
            log('Connected to telnet on {ip}'.format(ip=host), '+')

    log('Using location: {location}'.format(location=location.address), '+')
    log('Initial latitude: {latitude:.5f}'.format(latitude=location.latitude), '*')
    log('Initial longitude: {longitude:.5f}'.format(longitude=location.longitude), '*')
    log('Initial speed: {speed:.1f}km/h and update rate: {rate}s'.format(speed=args.s, rate=args.r), '*')

    # initial call to start thread
    renew_position()

if __name__ == '__main__':
    main()
    app.run(host=config['server']['host'], port=config.getint('server', 'port'))
