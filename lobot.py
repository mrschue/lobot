#!/usr/bin/python3

import json
import os
import subprocess
import boto3
from botocore.exceptions import ClientError
import datetime
import time
import socket
from shutil import get_terminal_size

STANDARD_ATTRIBUTES = ["Name", "KeyName", "InstanceId", "InstanceType", "PublicIpAddress", "Uptime", "State", "AvailabilityZone"]
RECOMMENDED_INSTANCE_TYPES = {"t3.nano":"Very cheap general purpose instance, good for testing workflows.",
        "t3.large":"Cheap general purpose instance, good for smaller tasks, e.g. mild preprocessing.",
        "c5.2xlarge":"Medium price CPU instance (vCPU=8), good for not-massively parallel tasks, e.g. small keras models, sklearn machine learning.",
        "g3s.xlarge":"Medium price nVidia (M60)  instance, good for almost all GPU tasks.",
        "p2.xlarge":"Medium price nVidia (K80) instance, useful if g3s.xlarge is already taken.",
        "r5.2xlarge":"Medium price RAM instance, good for memory-demanding tasks, e.g. preprocessing, expansion/reduction workflows. Has 64GB RAM.",
        "p3.2xlarge":"High price nVidia (V100) instance, good for demanding GPU tasks.",
        "r5.12xlarge":"High price RAM instance, good for extremely memory-demanding tasks. Has 384GB RAM.",
        "c5.18xlarge":"High price CPU instance (vCPU=72), good for highly parallel non-GPU tasks, e.g. hyperparameter-tuning on CPU models."}


def check_port(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = False
    try:
        sock.bind(("0.0.0.0", port))
        result = True
    except:
        result = False
    sock.close()
    return result

def timedelta_hours_minutes(timedelta):
    return timedelta.days * 24 + timedelta.seconds//3600, (timedelta.seconds//60)%60

def load_prices(used_instance_types):
    pricing = boto3.client("pricing")
    price_map = {}
    known_instance_types = []
    product_list = []

    for used_type in used_instance_types:
        if used_type not in known_instance_types:
            filters = [{'Type' :'TERM_MATCH', 'Field':'operatingSystem', 'Value':'Linux' },
                   {'Type' :'TERM_MATCH', 'Field':'location',        'Value':'US East (N. Virginia)'},
                   {'Type' :'TERM_MATCH', 'Field':'instanceType',        'Value':used_type},
                   {'Type' :'TERM_MATCH', 'Field':'currentGeneration',        'Value':'Yes'}]
        product_list += [json.loads(product) for product in pricing.get_products(ServiceCode="AmazonEC2", Filters=filters)["PriceList"]]

    for product in product_list:
        technical_info = product["product"]["attributes"]
        try:
            on_demand_info = product["terms"]["OnDemand"]
        except KeyError:
            continue
        funny_key = list(on_demand_info.keys())[0]
        if len(on_demand_info.keys()) > 1:
            print("ALERT - MANY FUNNY KEYS")
        on_demand_info = on_demand_info[funny_key]["priceDimensions"]
        funny_key = list(on_demand_info.keys())[0]
        if len(on_demand_info.keys()) > 1:
            print("ALERT - MANY FUNNY KEYS")
        on_demand_info = on_demand_info[funny_key] 
        price_desc = on_demand_info["description"]
        price_unit = on_demand_info["unit"]
        price_per_unit_in_usd = float(on_demand_info["pricePerUnit"]["USD"])
        if price_per_unit_in_usd == 0:
            continue
        info_dict = {"pricePerUnit":price_per_unit_in_usd, "unit":price_unit, "instanceFamily":technical_info["instanceFamily"]}
        price_map[technical_info["instanceType"]] = info_dict
        known_instance_types.append(technical_info["instanceType"])
    del pricing
    return price_map

def merge_price_map(instances, price_map):
    for idx, inst in enumerate(instances):
        info = price_map.get(inst["InstanceType"], None)
        if info is not None:
            inst.update(info)
        else:
            print("Warning: "+str(inst["InstanceType"])+" is not known")
    return instances

def get_current_instances(interesting_attributes=STANDARD_ATTRIBUTES, include_prices=True):
    assert("InstanceType" in interesting_attributes)
    ec2 = boto3.client("ec2")
    reservations = ec2.describe_instances()["Reservations"]
    used_types =[]
    instances = []
    for res in reservations:
        instances += res["Instances"]
    # Unpack tags and state
    for idx, inst in enumerate(instances):
        for attribute in interesting_attributes:
            if not attribute in inst:
                inst[attribute] = None
        if "State" in inst:
            inst["State"] = inst["State"]["Name"]
        if inst["InstanceType"] not in used_types:
            used_types.append(inst["InstanceType"])
        if "Uptime" in interesting_attributes:
            if inst["State"] != "running":
                uptime = timedelta_hours_minutes(datetime.timedelta(0))
            else:
                uptime = timedelta_hours_minutes(datetime.datetime.now(datetime.timezone.utc) - inst["LaunchTime"])
            inst["Uptime"] = "{}h {}m".format(*uptime)
        tags = inst["Tags"]
        for tag in tags:
            inst[tag["Key"]] = tag["Value"]
        inst.pop("Tags", None)
        placement = inst["Placement"]
        for k,v in placement.items():
            inst[k] = v
        instances[idx] = {k:v for k,v in inst.items() if k in interesting_attributes}
    if include_prices:
        price_map = load_prices(used_types)
        instances = merge_price_map(instances, price_map)
    del ec2
    return (instances, used_types)

def start_instance(instance, waiting_periods=7):
    if instance["State"] in ("running", "pending"):
        print("No need to start this one. Maybe have some patience.")
    else:
        ec2 = boto3.client("ec2")
        # Do a dryrun first to verify permissions
        response = None
        try:
            ec2.start_instances(InstanceIds=[instance["InstanceId"]], DryRun=True)
        except ClientError as e:
            if 'DryRunOperation' not in str(e):
                raise
        # Dry run succeeded, run start_instances without dryrun
        try:
            response = ec2.start_instances(InstanceIds=[instance["InstanceId"]], DryRun=False)
            print("START signal sent, waiting for reachability ...")
            waiter = ec2.get_waiter("instance_running")
            waiter.wait(InstanceIds=[instance["InstanceId"]])
            current_info = ec2.describe_instances(InstanceIds=[instance["InstanceId"]])["Reservations"][0]["Instances"][0]
            if "PublicIpAddress" in current_info:
                print("Instance reachable, address: "+current_info["PublicIpAddress"])
        except ClientError as e:
            print(e)
        del ec2
        return response

def stop_instance(instance):
    confirm_prompt =     {
        'type': 'confirm',
        'message': 'Do you really want to stop \"'+instance["Name"]+'\"?',
        'name': 'stop',
        'default': False,
    }
    chosen_confirmation = prompt(confirm_prompt)["stop"]
    if not chosen_confirmation:
        print(" ----> Canceling.")
        return 
    if instance["State"] in ("stopped", "stopping"):
        print("------> Instance is already stopped or stopping.")
    else:
        ec2 = boto3.client("ec2")
        # Do a dryrun first to verify permissions
        response = None
        try:
            ec2.stop_instances(InstanceIds=[instance["InstanceId"]], DryRun=True)
        except ClientError as e:
            if 'DryRunOperation' not in str(e):
                raise
        # Dry run succeeded, run start_instances without dryrun
        try:
            response = ec2.stop_instances(InstanceIds=[instance["InstanceId"]], DryRun=False)
            print("STOP signal sent, waiting for full stop. This might take a while.")
            waiter = ec2.get_waiter("instance_stopped")
            waiter.wait(InstanceIds=[instance["InstanceId"]])
            print("Instance stopped.")
        except ClientError as e:
            print(e)
        return response

def connect_instance(instance):
    # Check if key is available
    key_name = instance["KeyName"]
    key_path = os.path.dirname(os.path.realpath(__file__))+"/keys/"+key_name+".pem"
    if os.path.exists(key_path):
        subprocess.call(["ssh", "-i", key_path, "ec2-user@"+instance["PublicIpAddress"]])
    else:
        raise ValueError("Key"+key_name+".pem is not available in my keys folder")

def start_jupyter(instance, local_port=8888):
    # Check if key is available
    key_name = instance["KeyName"]
    key_path = os.path.dirname(os.path.realpath(__file__))+"/keys/"+key_name+".pem"
    if os.path.exists(key_path):
        output = str(subprocess.run(["ssh", "-i", key_path, "ec2-user@"+instance["PublicIpAddress"], "jupyter", "notebook", "list"], stdout=subprocess.PIPE).stdout).split("\\n")[1:-1]
        if len(output) == 0:
            print("Starting jupyter server remotely...")
            subprocess.run(["ssh", "-i", key_path, "ec2-user@"+instance["PublicIpAddress"], "screen", "-dm", "bash", "-c", "\"jupyter", "notebook", "--no-browser", "--port=8889\""]) 
            time.sleep(6)
            output = str(subprocess.run(["ssh", "-i", key_path, "ec2-user@"+instance["PublicIpAddress"], "jupyter", "notebook", "list"], stdout=subprocess.PIPE).stdout).split("\\n")[1:-1]
            print("\t ... done")
        else:
            print("Jupyter server found, did not start a new server.")
        if check_port(local_port):
            server_prompt = {
                'type': 'list',
                'name': 'server',
                'message': 'Port '+str(local_port)+' available. Connect?',
                'choices': output
            }
            jupyter_instance = prompt(server_prompt)["server"]
            remote_hostport = jupyter_instance.split("/")[2]
            command = ["nohup", "ssh", "-i", key_path, "-N", "-L", str(local_port)+":"+remote_hostport, "ec2-user@"+instance["PublicIpAddress"]]
            process = subprocess.Popen(command, preexec_fn=os.setpgrp)
            print("Port forwarding PID: "+str(process.pid))
            print("")
            print("\t\t\t\thttp://localhost:"+str(local_port))
        else:
            print("Local port "+str(local_port)+" is taken. Maybe you are already connected?")
    else:
        raise ValueError("Key"+key_name+".pem is not available in my keys folder")
    return output

def kill_jupyters(instance):
    key_name = instance["KeyName"]
    key_path = os.path.dirname(os.path.realpath(__file__))+"/keys/"+key_name+".pem"
    # UNFINISHED

def display_instances(instances):
    assert(len(instances) > 0)
    keys = sorted(instances[0].keys())
    instance_table = PrettyTable(keys)
    instances = sorted(instances, key=lambda x: (0 if x["State"] == "running" else 1, x["State"], 1./x["pricePerUnit"]), reverse=False)
    for instance in instances:
        items = sorted(instance.items(), key=lambda x: x[0])
        instance_table.add_row([v for k,v in items])
    print("\n\n")
    print(instance_table)
    print("\n\n")

def change_type(instance, available_instances=RECOMMENDED_INSTANCE_TYPES):
    assert(instance["State"] == "stopped")
    ec2 = boto3.client("ec2")
    choices = [k+" :: "+v for k, v in available_instances.items()]
    type_prompt = {
         'type': 'list',
         'name': 'type',
         'message': 'Current type: '+instance["InstanceType"]+'. Which type do you want instead?',
         'choices': choices
     }
    chosen_type = prompt(type_prompt)["type"].split(" :: ")[0]
    ec2.modify_instance_attribute(InstanceId=instance["InstanceId"], Attribute='instanceType', Value=chosen_type)

def change_name(instance):
    assert(instance["State"] == "stopped")
    ec2 = boto3.client("ec2")
    name_prompt = {
         'type': 'input',
         'name': 'instance_name',
         'message': 'Current name: '+instance["Name"]+'. Which name do you want instead?',
     }
    chosen_name = prompt(name_prompt)["instance_name"]
    confirm_prompt =     {
        'type': 'confirm',
        'message': 'Do you want to change the name \"'+instance["Name"]+'\" to \"'+chosen_name+'\"?',
        'name': 'change_name',
        'default': False,
    }
    chosen_confirmation = prompt(confirm_prompt)["change_name"]
    if not chosen_confirmation:
        print("-----------> Name was not changed.")
    else:
        new_name_tag = {"Key":"Name", "Value":chosen_name}
        ec2.create_tags(Resources=[instance["InstanceId"]], Tags=[new_name_tag])
        print("Name should be changed now!")
        time.sleep(1.5)

def deploy(instance):
    deploy_path = os.path.dirname(os.path.realpath(__file__))+"/deploy/"
    print("\nContent of \"deploy\" folder:")
    for filename in os.listdir(deploy_path):
        print("\t\t"+filename)
    print("\t\t - - -") 
    confirm_prompt =     {
        'type': 'confirm',
        'message': 'Do you want to copy the content of the \"deploy\" folder to the remote machine?',
        'name': 'deploy',
        'default': False,
    }
    chosen_confirmation = prompt(confirm_prompt)["deploy"]
    if chosen_confirmation:
        if not os.path.exists(deploy_path):
            print("No \"deploy\" folder in the script's directory \""+os.path.dirname(os.path.realpath(__file__)))
            return
        key_name = instance["KeyName"]
        key_path = os.path.dirname(os.path.realpath(__file__))+"/keys/"+key_name+".pem"
        command = ["scp", "-i", key_path, "-r", deploy_path+".", "ec2-user@"+instance["PublicIpAddress"]+":lobot/deploy/"]
        if os.path.exists(key_path):
            ls_command = ["ssh", "-i", key_path, "ec2-user@"+instance["PublicIpAddress"], "ls", "-ll", "~/lobot/deploy"]
            ls_returncode = subprocess.call(ls_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if ls_returncode == 2:
                return_code = subprocess.call(["ssh", "-i", key_path, "ec2-user@"+instance["PublicIpAddress"], "mkdir", "~/lobot", ";", "mkdir", "~/lobot/deploy"])
            if subprocess.call(command) == 0:
                print("Copied to \"~/lobot/deploy\" on remote machine.")
        else:
            raise ValueError("Key"+key_name+".pem is not available in my keys folder")


def fetch(instance):
    fetch_path = os.path.dirname(os.path.realpath(__file__))+"/fetch/"
    key_name = instance["KeyName"]
    key_path = os.path.dirname(os.path.realpath(__file__))+"/keys/"+key_name+".pem"
    command = ["ssh", "-i", key_path, "ec2-user@"+instance["PublicIpAddress"], "ls", "-ll", "~/lobot/fetch"]
    if os.path.exists(key_path):
        print("Output of \"ls -ll ~/lobot/fetch\" on remote machine:")
        return_code = subprocess.call(command)
        if return_code == 2:
            return_code = subprocess.call(["ssh", "-i", key_path, "ec2-user@"+instance["PublicIpAddress"], "mkdir", "~/lobot", ";", "mkdir", "~/lobot/fetch"])
            print("\"~/lobot/fetch\" folder created remotely, is empty")
            return
    else:
        raise ValueError("Key"+key_name+".pem is not available in my keys folder")
    confirm_prompt =     {
        'type': 'confirm',
        'message': 'Do you want to copy the content of the remote \"~/lobot/fetch\" folder to the local machine?',
        'name': 'fetch',
        'default': False,
    }
    chosen_confirmation = prompt(confirm_prompt)["fetch"]
    if chosen_confirmation:
        if not os.path.exists(fetch_path):
            print("No \"fetch\" folder in the script's directory \""+os.path.dirname(os.path.realpath(__file__)))
            return
        command = ["scp", "-i", key_path, "-r", "ec2-user@"+instance["PublicIpAddress"]+":lobot/fetch/", fetch_path]
        if os.path.exists(key_path):
            subprocess.call(command)
        else:
            raise ValueError("Key"+key_name+".pem is not available in my keys folder")
     
   
def ask_instance(instances):
        sorted_list = sorted(instances, key=lambda x: x["State"])
        choices = [inst["InstanceId"]+" :: ("+inst["State"]+", "+inst["Name"]+")" for inst in sorted_list]
        instance_prompt = {
            'type': 'list',
            'name': 'instance',
            'message': 'Which instance do you want to use?',
            'choices': choices
        }
        answers = prompt(instance_prompt)
        return answers['instance'].split(" :: ")[0]

if __name__ == "__main__":
    from PyInquirer import style_from_dict, Token, prompt
    from prettytable import PrettyTable

    # Check if there is a "keys" folder. If not, create one
    print("\n")
    created_folder = False
    key_path = os.path.dirname(os.path.realpath(__file__))+"/keys"
    if not os.path.isdir(key_path):
        print("No \"keys\" folder. Creating one ...")
        os.mkdir(key_path)
        create_folder = True
    fetch_path = os.path.dirname(os.path.realpath(__file__))+"/fetch"
    if not os.path.isdir(fetch_path):
        print("No \"fetch\" folder. Creating one ...")
        os.mkdir(fetch_path)
        created_folder = True
    deploy_path = os.path.dirname(os.path.realpath(__file__))+"/deploy"
    if not os.path.isdir(deploy_path):
        print("No \"deploy\" folder. Creating one ...")
        os.mkdir(deploy_path)
        created_folder = True
    if created_folder:
        input("\nENTER to continue ..")

    while True:
        os.system("clear")
        print("Loading instances")
        instances, used_types = get_current_instances()
        print("\t ... done")
        display_instances(instances)
        time.sleep(2)
        # Choose instance
        chosen_instance = ask_instance(instances)
        for inst in instances:
            if inst["InstanceId"] == chosen_instance:
                chosen_instance = inst
        # Choose action
        options = []
        if chosen_instance["State"] == "running" and chosen_instance["PublicIpAddress"] is not None:
            options.append("Open shell (SSH)")
            options.append("Jupyter")
            options.append("Deploy")
            options.append("Fetch")
            options.append("Stop")
        elif chosen_instance["State"] in ("terminated", "terminating"):
            options = ["Nothing to do here."]
        else:
            options.append("Start")
            options.append("Change type")
            options.append("Change name")
        time.sleep(2)
        chosen_action = prompt({'type':"list", "name":"action", "message": "What do you want to do?", "choices":options})["action"]
        if chosen_action == "Start":
            response = start_instance(chosen_instance)
        if chosen_action == "Stop":
            response = stop_instance(chosen_instance)
        if chosen_action == "Open shell (SSH)":
            connect_instance(chosen_instance)
        if chosen_action == "Jupyter":
            process = start_jupyter(chosen_instance)
        if chosen_action == "Kill Jupyters":
            kill_jupyters(chosen_instance)
        if chosen_action == "Change type":
            change_type(chosen_instance)
        if chosen_action == "Change name":
            change_name(chosen_instance)
        if chosen_action == "Deploy":
            deploy(chosen_instance)
        if chosen_action == "Fetch":
            fetch(chosen_instance)
        time.sleep(1.5)
        input("\n\nENTER to reload script ..")
