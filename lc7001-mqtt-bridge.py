#!/usr/bin/python3
#
# pip3 install paho-mqtt tendo
#
#
# To run in crontab, do the same again as root
# sudo su -
# pip3 install paho-mqtt tendo
#
#
# CRONTAB:
# @reboot sleep 60;sudo --user=pi /home/pi/lc7001-mqtt-bridge/lc7001-mqtt-bridge.py
# 0 * * * * sudo --user=pi /home/pi/lc7001-mqtt-bridge/lc7001-mqtt-bridge.py
#

import re, requests, sys, os, logging, socket
import json, time, threading, argparse
import paho.mqtt.client as paho
from tendo import singleton
from logging.handlers import RotatingFileHandler

try:
    from ConfigParser import RawConfigParser
except ImportError as e:
    from configparser import RawConfigParser

cmd_id = 1
zoneList = {}
zoneType = {}
config = {}

# -------------------- LoadConfig -----------------------------------
def LoadConfig(conf_file):
    global config 
    
    try:
        configParser = RawConfigParser()
        configParser.read(conf_file)
    except Exception as e1:
        logger.critical("Error in LoadConfig: " + str(e1))
        return False
        
    parameters = {'DebugLevel': str, 'MQTT_Server': str, 'MQTT_Port': int, 'MQTT_User': str, 'MQTT_Password': str, 'LC7001_Server': str, 'LC7001_Port': int, 'EnableDiscovery': bool}
    for key, type in parameters.items():
        try:
            if configParser.has_option("General", key):
                config[key] = ReadValue(key, return_type=type, section="General", configParser=configParser)
        except Exception as e1:
            logger.critical("Missing config file or config file entries in Section General for key "+key+": " + str(e1))
            return False

    return True

def ReadValue(Entry, return_type = str, default = None, section = None, NoLog = False, configParser = None):
    try:
        if configParser.has_option(section, Entry):
            if return_type == str:
                return configParser.get(section, Entry)
            elif return_type == bool:
                return configParser.getboolean(section, Entry)
            elif return_type == float:
                return configParser.getfloat(section, Entry)
            elif return_type == int:
                return configParser.getint(section, Entry)
            else:
                logger.error("Error in MyConfig:ReadValue: invalid type:" + str(return_type))
                return default
        else:
            return default
    except Exception as e1:
        if not NoLog:
            logger.critical("Error in MyConfig:ReadValue: " + Entry + ": " + str(e1))
        return default


# -------------------- Handle Messages -----------------------------------


def printMessage(msg):
    logger.info("received message: "+json.dumps(msg, indent=4, sort_keys=True))
    
def sendMessageToLC7001(cmd):
    msg_send = json.dumps(cmd)+chr(0)
    # logger.debug("sending message: "+str(cmd))
    try:
        s.send(msg_send.encode())
    except Exception as e1:
        logger.critical("Exception Occured: " + str(e1))
        logger.critical("Exception Occured on Message: " + str(msg_send))
        logger.critical("Shutting down, so cron can restart this process to reconnect")
        os._exit(1)
    
    logger.info("sent message: "+str(cmd))
   
def haState(stateBool):
    if stateBool == True:
       return "ON"
    else:
       return "OFF"
      
def lcState(stateStr):
    if stateStr.lower() in ['on', '1', 't', 'y', 'yes', 'true']:
       return True
    else:   
       return False
       
def sendMQTT(ZID, status):
    logger.info("PUBLISHING to MQTT: home/legrand/light/state/" + str(ZID) + " = " + haState(status))
    t.publish("home/legrand/light/state/"+str(ZID),haState(status),retain=True)

def sendRawMQTT(topic, msg):
    logger.info("PUBLISHING to MQTT: " + topic + " = " + msg)
    t.publish(topic,msg,retain=True)


def receiveMessageFromLC7001():
    global zoneList 
    global zoneType 
    global config
    
    for msg_raw in iter(lambda: s.recv(8192).decode(), ''):
        try:
            ### Sometime 2 messages come as one... MUST BE FIXED
            msgs = json.loads("["+msg_raw.replace(chr(0), "").replace("}{", "},{")+"]")
            for msg in msgs:
                logger.debug(str(msg))
                if ("Service" not in msg):
                   logger.debug("unknown message (does not contaion Service tag): "+str(msg))
                elif (msg["Service"] == "ping"):
                   True
                   #ignore PING
                elif ("Got NTP" in msg["Service"]) and (msg["Status"] == True):
                   True
                   #ignore successfull NTP Sync
                elif (msg["Service"] == "EliotErrors"):
                   True
                   #ignore ELIOT STATUS
                elif (msg["Service"] == "BroadcastMemory") or (msg["Service"] == "BroadcastDiagnostics"):
                   True
                   #ignore diagnostics
                elif (msg["Service"] == "SetZoneProperties"):
                   if (msg["Status"] != "Success"):
                       logger.critical("Zone Status change was not successfully: "+str(msg))
                elif (msg["Service"] == "SetSystemProperties"):
                   if (msg["Status"] != "Success"):
                       logger.critical("System Status change was not successfully: "+str(msg))
                elif (msg["Service"] == "ListZones"):
                   zoneList = {}
                   zoneType = {}
                   for z in msg["ZoneList"]:
                     zoneList[z["ZID"]] = "" 
                   logger.debug("ZoneList successfully set: "+str(zoneList))
                elif (msg["Service"] == "ReportZoneProperties"):
                   logger.info(str(msg["ZID"]) + " -> Name : " + msg["PropertyList"]["Name"] + " | Status: " + str(msg["PropertyList"]["Power"])  + " | Type: " + msg["PropertyList"]["DeviceType"])
                   zoneList[msg["ZID"]] = msg["PropertyList"]["Name"]
                   zoneType[msg["ZID"]] = msg["PropertyList"]["DeviceType"]
                   logger.debug("ZoneList successfully set: "+str(zoneList))
                   if config["EnableDiscovery"]: 
                       topic = str(msg["PropertyList"]["Name"]).lower().replace(" ", "_")
                       if (msg["PropertyList"]["DeviceType"] == "Switch"):
                          sendRawMQTT("homeassistant/light/"+topic+"/config", '{"name": "'+msg["PropertyList"]["Name"]+'", "command_topic": "home/legrand/light/command/'+str(msg["ZID"])+'", "state_topic": "home/legrand/light/state/'+str(msg["ZID"])+'"}')
                       elif (msg["PropertyList"]["DeviceType"] == "Dimmer"):
                          sendRawMQTT("homeassistant/light/"+topic+"/config", '{"name": "'+msg["PropertyList"]["Name"]+'", "command_topic": "home/legrand/light/command/'+str(msg["ZID"])+'", "state_topic": "home/legrand/light/state/'+str(msg["ZID"])+'", "brightness_command_topic": "home/legrand/light/brightness_command/'+str(msg["ZID"])+'", "brightness_state_topic": "home/legrand/light/brightness/'+str(msg["ZID"])+'", "brightness_scale": 100}')
                       else:
                          logger.error("Unknown Type for ZID: "+str(msg["ZID"])+", Type: "+msg["PropertyList"]["DeviceType"])
                       time.sleep(1)
                   sendMQTT(msg["ZID"], msg["PropertyList"]["Power"])
                elif (msg["Service"] == "ZonePropertiesChanged"):
                   propertyList = msg["PropertyList"]
                   if (zoneType[msg["ZID"]] == "Switch") and (len(propertyList) == 2) and ("Power" in propertyList):
                      logger.info(str(msg["ZID"]) + " -> Power: " + str(msg["PropertyList"]["Power"]))
                      sendMQTT(msg["ZID"], msg["PropertyList"]["Power"])
                   elif (zoneType[msg["ZID"]] == "Dimmer") and (len(propertyList) == 2) and ("Power" in propertyList) and ("PowerLevel" in propertyList):
                      logger.info(str(msg["ZID"]) + " -> Power: " + str(msg["PropertyList"]["Power"]) +  " -> PowerLevel: " + str(msg["PropertyList"]["PowerLevel"]))
                      sendMQTT(msg["ZID"], msg["PropertyList"]["Power"])
                      sendRawMQTT("home/legrand/light/brightness/"+str(msg["ZID"]), str(msg["PropertyList"]["PowerLevel"]))
                   else:
                      printMessage(msg)
                elif (msg["Service"] == "ZoneAdded"):
                   cmd = {"ID": cmd_id, "Service": "ReportZoneProperties", "ZID": msg["ZID"]}
                   logger.info("sending message: "+str(cmd))
                   sendMessageToLC7001(cmd)
                   cmd_id = cmd_id+1
                elif (msg["Service"] == "SystemPropertiesChanged"):
                   propertyList = msg["PropertyList"]
                   if (len(propertyList) == 1) and ("CurrentTime" in propertyList):
                      True
                      #ignore minute change
                   elif  (len(propertyList) == 1) and ("AddALight" in propertyList):
                      if msg["PropertyList"]["AddALight"] == True:
                         logger.info("LC7001 entered discovery mode for new Lights")
                         sendRawMQTT("home/legrand/switch/state", "ON")
                      else:
                         logger.info("LC7001 exited discovery mode for new Lights")
                         sendRawMQTT("home/legrand/switch/state", "OFF")
                   else:
                      printMessage(msg)
                else:
                   printMessage(msg)
        except Exception as e1:
            logger.critical("Exception Occured: " + str(e1))
            logger.critical("Exception Occured on Message: " + str(msg_raw))
            
        
def receiveMessageFromSTDIN():
    global cmd_id
    
    while True:
        try:
            msg = input("Please enter your message: ")
            
            if (msg.strip() != ""):
                my_msg = msg.strip().split(" ")
                cmd = {"ID": cmd_id}
                cmd["Service"] = my_msg[0]
                if (len(my_msg) == 2):
                    cmd["ZID"] = int(my_msg[1])
                if (len(my_msg) == 3) and (my_msg[0] == "SetZoneProperties"):
                    cmd["ZID"] = int(my_msg[1])
                    cmd1_state = my_msg[2].lower() in ['true', '1', 't', 'y', 'yes']
                    cmd1 = {"Power": cmd1_state}
                    cmd["PropertyList"] = cmd1
                    
                sendMessageToLC7001(cmd)
                cmd_id = cmd_id+1
        except Exception as e1:
            logger.critical("Exception Occured: " + str(e1))
            
def sendStartupInfo():
    #i = 5 ### Give Home Assistant time to start  # MQTT Message retain makes this irrelevant
    #while (i > 0):
    getStatupState()
    #    i = i - 1
    #    time.sleep(60)

def on_connect(client, userdata, flags, rc):
    global config
    
    logger.info("Connected to MQTT with result code "+str(rc))
    t.subscribe("home/legrand/light/command/+")
    t.subscribe("home/legrand/light/brightness_command/+")
    t.subscribe("home/legrand/switch/config")
    if config["EnableDiscovery"]:
        restart_thread = threading.Thread(target=sendStartupInfo)
        restart_thread.daemon = True
        restart_thread.start()
        logger.info("Started Restart Thread to send initialization Information to Home Assistant")           
    
def receiveMessageFromMQTT(client, userdata, message):
    global cmd_id

    logger.info("starting receiveMessageFromMQTT")
    try:
        msg = str(message.payload.decode("utf-8"))
        topic = message.topic
        logger.info("message received from MQTT: "+topic+" = "+msg)
       
        type = topic.split("/")[3]
        if type == "command":
            ZID = int(topic.split("/")[4])
            state = lcState(msg)    
        
            cmd = {"ID": cmd_id, "Service": "SetZoneProperties", "ZID": ZID}
            cmd1 = {"Power": state}
            cmd["PropertyList"] = cmd1
            
            logger.info("sending message: "+str(cmd))
            sendMessageToLC7001(cmd)
            cmd_id = cmd_id+1
        if type == "brightness_command":
            ZID = int(topic.split("/")[4])
            level = int(msg)

            cmd = {"ID": cmd_id, "Service": "SetZoneProperties", "ZID": ZID}
            cmd1 = {"PowerLevel": level}
            cmd["PropertyList"] = cmd1

            logger.info("sending message: "+str(cmd))
            sendMessageToLC7001(cmd)
            cmd_id = cmd_id+1            
        elif type == "config":
            state = lcState(msg)
            
            cmd = {"ID": cmd_id, "Service": "SetSystemProperties"}
            cmd1 = {"AddALight": state}
            cmd["PropertyList"] = cmd1
            
            logger.info("sending message: "+str(cmd))
            sendMessageToLC7001(cmd)
            cmd_id = cmd_id+1

    except Exception as e1:
        logger.critical("Exception Occured: " + str(e1))
        
    logger.info("finishing receiveMessageFromMQTT")

def getStatupState():
    global cmd_id
    global zoneList 
    global zoneType 
    
    sendRawMQTT("homeassistant/switch/legrand_discovery/config", '{"name": "Legrand Discovery", "command_topic": "home/legrand/switch/config", "state_topic": "home/legrand/switch/state"}')
    sendRawMQTT("home/legrand/switch/state", "OFF")
    
    zoneList = {}
    zoneType = {}
    cmd = {"ID": cmd_id, "Service": "ListZones"}
    logger.info("sending message: "+str(cmd))
    sendMessageToLC7001(cmd)
    cmd_id = cmd_id+1
    while len(zoneList) == 0:
        time.sleep(0.1)
    for z in zoneList:
        cmd = {"ID": cmd_id, "Service": "ReportZoneProperties", "ZID": z}
        logger.info("sending message: "+str(cmd))
        sendMessageToLC7001(cmd)
        cmd_id = cmd_id+1
        
        
if __name__ == '__main__':
    LEVELS = {'debug': logging.DEBUG,
              'info': logging.INFO,
              'warning': logging.WARNING,
              'error': logging.ERROR,
              'critical': logging.CRITICAL}

    curr_path = os.path.dirname(__file__)
    curr_name = os.path.basename(__file__)
    log_name = curr_name.replace(".py", ".log")
    log_file = curr_path+"/"+log_name
    log_formatter = logging.Formatter('%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s')

    rotatingHandler = RotatingFileHandler(log_file, mode='a', maxBytes=1*1024*1024, backupCount=2, encoding=None, delay=0)
    rotatingHandler.setFormatter(log_formatter)
    rotatingHandler.setLevel(logging.INFO)

    logger = logging.getLogger('root')
    logger.addHandler(rotatingHandler)

    # Make sure we are not running already. Otherwise Exit
    try:
       tmp = logging.getLogger().level
       logging.getLogger().setLevel(logging.CRITICAL) # we do not want to see the warning
       me = singleton.SingleInstance() # will sys.exit(-1) if other instance is running
       logging.getLogger().setLevel(tmp)
    except:
       logging.getLogger().setLevel(logging.INFO)
       logger.info("Another instance is already running. quiting...")
       exit()

    # Now read the config file
    parser = argparse.ArgumentParser(description='Legrand LC7001 to MQTT Integration for Home Assistant.')
    parser.add_argument('-config', '-c', dest='ConfigFile', default=curr_path+'/lc7001-mqtt-bridge.conf', help='Name of the Config File (incl full Path)')
    args = parser.parse_args()
    
    if args.ConfigFile == None:
        conf_name = curr_name.replace(".py", ".conf")
        conf_file = curr_path+"/"+conf_name
    else:
        conf_file = args.ConfigFile

    if not os.path.isfile(conf_file):
        logger.info("Creating new config file : " + conf_file)
        defaultConfigFile = curr_path+'/defaultConfig.conf'
        if not os.path.isfile(defaultConfigFile):
            logger.critical("Failure to create new config file: "+defaultConfigFile)
            sys.exit(1)
        else:
            copyfile(defaultConfigFile, conf_file)

    if not LoadConfig(conf_file):
        logger.critical("Failure to load configuration parameters")
        sys.exit(1)
        
    level = LEVELS.get(config["DebugLevel"], logging.WARNING)
    logging.getLogger().setLevel(level)
    rotatingHandler.setLevel(level)
     
    # And connect to MQTT  
    t = paho.Client(client_id="lc7001-mqtt-bridge")                           #create client object
    t.username_pw_set(username=config["MQTT_User"],password=config["MQTT_Password"])
    t.connect(config["MQTT_Server"],config["MQTT_Port"])
    logger.info("Connected to MQTT on "+config["MQTT_Server"]+":"+str(config["MQTT_Port"]))

    s = socket.socket()
    s.connect((config["LC7001_Server"], config["LC7001_Port"]))
    logger.info("Connected to Legrand LC7001")

    background_thread = threading.Thread(target=receiveMessageFromLC7001)
    background_thread.daemon = True
    background_thread.start()
    logger.info("Started Listener Thread to listen to messages from Legrand LC7001")
    
    #background_thread = threading.Thread(target=receiveMessageFromSTDIN)
    #background_thread.daemon = True
    #background_thread.start()
    #logger.info("Started Listener Thread to listen to messages from Keyboard")
    
    t.on_connect = on_connect
    t.on_message=receiveMessageFromMQTT
    logger.info("Starting Listener Thread to listen to messages from MQTT")
    t.loop_start()
    
    if not config["EnableDiscovery"]:
        getStatupState()

    while True:
       time.sleep(60)

    t.loop_stop()
    logger.info("Stop Listener Thread to listen to messages from MQTT")
    
    del me
    
    sys.exit()
