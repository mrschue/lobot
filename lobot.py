#!/usr/bin/python3

import json
from PyInquirer import style_from_dict, prompt
from prettytable import PrettyTable
import os
import subprocess
import boto3
from botocore.exceptions import ClientError
import datetime
import time
import socket

GLOBAL_CONFIG = {}

# Global dictionary that maps AWS-usernames to a description of the images that uses them.
USERNAME_TO_AMI = {"ec2-user": "For Amazon Linux AMI, Fedora AMI, Suse AMI",
                  "ubuntu": "For Ubuntu AMI",
                  "centos": "For Centos AMI",
                  "admin": "For Debian AMI"}

# Attributes lobot will fetch from the AWS database
STANDARD_ATTRIBUTES = ["Name", "KeyName", "InstanceId", "InstanceType", "PublicIpAddress", "Uptime", "State"]


# This dictionary maps region codes to readable region names.
# https://docs.aws.amazon.com/general/latest/gr/rande.html
REGION_TO_READABLE_NAME = {
        "us-east-1": "US East (N. Virginia)",
        "us-east-2": "US East (Ohio)",
        "us-west-1": "US West (N. California)",
        "us-west-2": "US West (Oregon)",
        "ap-south-1": "Asia Pacific (Mumbai)",
        "ap-northeast-3": "Asia Pacific (Osaka Local)",
        "ap-northeast-2": "Asia Pacific (Seoul)",
        "ap-southeast-1": "Asia Pacific (Singapore)",
        "ap-southeast-2": "Asia Pacific (Sydney)",
        "ap-northeast-1": "Asia Pacific (Tokyo)",
        "ca-central-1": "Canada (Central)",
        "cn-north-1": "China (Beijing)",
        "cn-northwest-1": "China (Ningxia)",
        "eu-central-1": "EU (Frankfurt)",
        "eu-west-1": "EU (Ireland)",
        "eu-west-2": "EU (London)",
        "eu-west-3": "EU (Paris)",
        "eu-north-1": "EU (Stockholm)",
        "sa-east-1": "South America (São Paulo)"}

def read_config(filepath=os.path.dirname(os.path.realpath(__file__))+"/config.cfg"):
    config_dict = {}
    with open(filepath, "r") as config_file:
        config_content = config_file.readlines()
    for line in config_content:
        if line in ("", "\n"):
            continue
        if line.strip()[0] == "#":
            continue
        key, value = line.split(":", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if value in ("True", "true", "1"):
            value = True
        if value in ("False", "false", "0"):
            value = False
        config_dict[key] = value
    return config_dict

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

def load_prices(used_instance_types, region_name):
    pricing = boto3.client("pricing")
    price_map = {}
    known_instance_types = []
    product_list = []

    for used_type in used_instance_types:
        if used_type not in known_instance_types:
            try:
                location_name = REGION_TO_READABLE_NAME[region_name]
            except KeyError:
                raise KeyError("Region "+str(region_name)+" does not have a readable name. Please check https://docs.aws.amazon.com/general/latest/gr/rande.html and update the REGION_TO_READABLE_NAME dictionary")
            filters = [{'Type' :'TERM_MATCH', 'Field':'operatingSystem', 'Value':'Linux' },
                   {'Type' :'TERM_MATCH', 'Field':'location',        'Value': location_name},
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
        info_dict = {"pricePerUnit (*)":price_per_unit_in_usd, "unit":price_unit, "instanceFamily":technical_info["instanceFamily"]}
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

def imageid_to_name(image_id):
    ec2 = boto3.client("ec2")
    image_info = ec2.describe_images(ImageIds=[image_id])["Images"][0]
    image_name = image_info.get("Name", "")
    return image_name

def get_current_instances(interesting_attributes=STANDARD_ATTRIBUTES, include_prices=True, region_name=None):
    assert("InstanceType" in interesting_attributes)
    if region_name is None:
        ec2 = boto3.client("ec2")
        region_name = ec2.meta.region_name
    else:
        ec2 = boto3.client("ec2", region_name=region_name)
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
        try:
            tags = inst["Tags"]
            for tag in tags:
                inst[tag["Key"]] = tag["Value"]
            inst.pop("Tags", None)
        except KeyError:
            inst["Name"] = ""
        try:
            if "ImageName" in interesting_attributes:
                image_id = inst["ImageId"]
                image_name = imageid_to_name(image_id)
                inst.pop("ImageId", None)
                inst["ImageName"] = image_name
        except KeyError:
            inst["ImageName"] = ""
        placement = inst["Placement"]
        for k,v in placement.items():
            inst[k] = v
        instances[idx] = {k:v for k,v in inst.items() if k in interesting_attributes}
    if include_prices:
        price_map = load_prices(used_types, region_name=region_name)
        instances = merge_price_map(instances, price_map)
    del ec2
    return (instances, used_types, region_name)

def start_instance(instance, region_name, waiting_periods=7):
    if instance["State"] in ("running", "pending"):
        print("No need to start this one. Maybe have some patience.")
    else:
        ec2 = boto3.client("ec2", region_name=region_name)
        # Do a dryrun first to verify permissions
        response = None
        try:
            ec2.start_instances(InstanceIds=[instance["InstanceId"]], DryRun=True)
        except ClientError as e:
            if 'DryRunOperation' not in str(e):
                raise
        # Dry run succeeded, run start_instances without dry run
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

def stop_instance(instance, region_name):
    confirm_prompt =     {
        'type': 'confirm',
        'message': 'Do you really want to stop \"'+instance["Name"]+'\"?',
        'name': 'stop',
        'default': False,
    }
    chosen_confirmation = prompt.prompt(confirm_prompt)["stop"]
    if not chosen_confirmation:
        print(" ----> Canceling.")
        return 
    if instance["State"] in ("stopped", "stopping"):
        print("------> Instance is already stopped or stopping.")
    else:
        ec2 = boto3.client("ec2", region_name=region_name)
        response = None
        try:
            ec2.stop_instances(InstanceIds=[instance["InstanceId"]], DryRun=True)
        except ClientError as e:
            if 'DryRunOperation' not in str(e):
                raise
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
        subprocess.call(["ssh", "-i", key_path, GLOBAL_CONFIG["aws_username"]+"@"+instance["PublicIpAddress"]])
    else:
        raise ValueError("Key"+key_name+".pem is not available in my 'keys' folder.")

def start_jupyter(instance, local_port=8889):
    # Check onif key is available
    key_name = instance["KeyName"]
    key_path = os.path.dirname(os.path.realpath(__file__))+"/keys/"+key_name+".pem"
    if os.path.exists(key_path):
        output = str(subprocess.run(["ssh", "-i", key_path, GLOBAL_CONFIG["aws_username"]+"@"+instance["PublicIpAddress"], "jupyter", "notebook", "list"], stdout=subprocess.PIPE).stdout).split("\\n")[1:-1]
        if len(output) == 0:
            print("Starting jupyter server remotely...")
            subprocess.run(["ssh", "-i", key_path, GLOBAL_CONFIG["aws_username"]+"@"+instance["PublicIpAddress"], "screen", "-dm", "bash", "-c", "\"jupyter", "notebook", "--no-browser", "--port=8889\""])
            time.sleep(3)
            output = str(subprocess.run(["ssh", "-i", key_path, GLOBAL_CONFIG["aws_username"]+"@"+instance["PublicIpAddress"], "jupyter", "notebook", "list"], stdout=subprocess.PIPE).stdout).split("\\n")[1:-1]
            print("\t ... done")
        else:
            print("Jupyter server found, did not start a new server.")
        one_up = 0
        while (one_up < 3):
            if check_port(local_port + one_up):
                server_prompt = {
                    'type': 'list',
                    'name': 'server',
                    'message': 'Port '+str(local_port + one_up)+' available. Connect?',
                    'choices': output
                }
                jupyter_instance = prompt.prompt(server_prompt)["server"]
                remote_hostport = jupyter_instance.split("/")[2]
                command = ["nohup", "ssh", "-i", key_path, "-N", "-L", str(local_port + one_up)+":"+remote_hostport, GLOBAL_CONFIG["aws_username"]+"@"+instance["PublicIpAddress"]]
                process = subprocess.Popen(command, preexec_fn=os.setpgrp)
                print("Port forwarding PID: "+str(process.pid))
                print(jupyter_instance.replace(str(remote_hostport), str(local_port + one_up), 1))
                print("")
                break
            else:
                print("Local port "+str(local_port)+" is taken. Maybe you are already connected?")
                one_up += 1
    else:
        raise ValueError("Key"+key_name+".pem is not available in my keys folder")
    return output

def change_remote_username():
    global GLOBAL_CONFIG
    available_names = [k+"  -  "+v for k, v in USERNAME_TO_AMI.items()]
    username_prompt = {
         'type': 'list',
         'name': 'username',
         'message': 'Current username: '+GLOBAL_CONFIG["aws_username"]+'. Which username do you want use instead?',
         'choices': available_names
     }
    chosen_name = prompt.prompt(username_prompt)["username"].split("  -  ")[0]
    GLOBAL_CONFIG["aws_username"] = chosen_name


def kill_jupyters(instance):
    key_name = instance["KeyName"]
    key_path = os.path.dirname(os.path.realpath(__file__))+"/keys/"+key_name+".pem"
    # UNFINISHED

def display_instances(instances, region_name):
    print("\n")
    if region_name is not None:
        try:
            location_name = REGION_TO_READABLE_NAME[region_name]
        except KeyError:
            raise KeyError("Region "+str(region_name)+" does not have a readable name. Please check https://docs.aws.amazon.com/general/latest/gr/rande.html and update the REGION_TO_READABLE_NAME dictionary")
        print("Instances for region: \n\t\t"+str(region_name)+" ["+location_name+"]\n")
    if len(instances) > 0:
        keys = sorted(instances[0].keys())
        instance_table = PrettyTable(keys)
        instances = sorted(instances, key=lambda x: (0 if x["State"] == "running" else 1, x["State"]), reverse=False)
        for instance in instances:
            items = sorted(instance.items(), key=lambda x: x[0])
            instance_table.add_row([v for k,v in items])
        print(instance_table)
        if GLOBAL_CONFIG["load_prices"]:
            print("\t(*)\tlisted prices are in $ and for on-demand Linux (w/o SQL) in region '"+region_name+"' only.\n\t\t They might be unreliable in some cases - please confirm prices at: https://aws.amazon.com/de/ec2/pricing/on-demand/")
        print("\n\n")
    else:
        print("\n\n")
        if region_name is not None:
            try:
                location_name = REGION_TO_READABLE_NAME[region_name]
            except KeyError:
                raise KeyError("Region "+str(region_name)+" does not have a readable name. Please check https://docs.aws.amazon.com/general/latest/gr/rande.html and update the REGION_TO_READABLE_NAME dictionary")
            print("No instances in region '"+str(region_name)+"' ["+location_name+"] available.")
        else:
            print("No instances in this region.")
        print("\n\n")

def change_type(instance, region_name, available_instances):
    assert(instance["State"] == "stopped")
    ec2 = boto3.client("ec2", region_name=region_name)
    choices = [k+" :: "+v for k, v in available_instances.items()]
    type_prompt = {
         'type': 'list',
         'name': 'type',
         'message': 'Current type: '+instance["InstanceType"]+'. Which type do you want instead?',
         'choices': choices
     }
    chosen_type = prompt.prompt(type_prompt)["type"].split(" :: ")[0]
    ec2.modify_instance_attribute(InstanceId=instance["InstanceId"], Attribute='instanceType', Value=chosen_type)

def change_name(instance, region_name):
    ec2 = boto3.client("ec2", region_name)
    name_prompt = {
         'type': 'input',
         'name': 'instance_name',
         'message': 'Current name: '+instance["Name"]+'. Which name do you want instead?',
     }
    chosen_name = prompt.prompt(name_prompt)["instance_name"]
    confirm_prompt =     {
        'type': 'confirm',
        'message': 'Do you want to change the name \"'+instance["Name"]+'\" to \"'+chosen_name+'\"?',
        'name': 'change_name',
        'default': False,
    }
    chosen_confirmation = prompt.prompt(confirm_prompt)["change_name"]
    if not chosen_confirmation:
        print("-----------> Name was not changed.")
    else:
        new_name_tag = {"Key":"Name", "Value":chosen_name}
        ec2.create_tags(Resources=[instance["InstanceId"]], Tags=[new_name_tag])
        print("Name should be changed now!")
        time.sleep(0.5)

def deploy(instance):
    print("?")
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
    chosen_confirmation = prompt.prompt(confirm_prompt)["deploy"]
    if chosen_confirmation:
        if not os.path.exists(deploy_path):
            print("No \"deploy\" folder in the script's directory \""+os.path.dirname(os.path.realpath(__file__)))
            return
        key_name = instance["KeyName"]
        key_path = os.path.dirname(os.path.realpath(__file__))+"/keys/"+key_name+".pem"
        command = ["scp", "-i", key_path, "-r", deploy_path+".", GLOBAL_CONFIG["aws_username"]+"@"+instance["PublicIpAddress"]+":lobot/deploy/"]
        if os.path.exists(key_path):
            ls_command = ["ssh", "-i", key_path, GLOBAL_CONFIG["aws_username"]+"@"+instance["PublicIpAddress"], "ls", "-ll", "~/lobot/deploy"]
            ls_returncode = subprocess.call(ls_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if ls_returncode == 2:
                return_code = subprocess.call(["ssh", "-i", key_path, GLOBAL_CONFIG["aws_username"]+"@"+instance["PublicIpAddress"], "mkdir", "~/lobot", ";", "mkdir", "~/lobot/deploy"])
            if subprocess.call(command) == 0:
                print("Copied to \"~/lobot/deploy\" on remote machine.")
        else:
            raise ValueError("Key"+key_name+".pem is not available in my keys folder")


def fetch(instance):
    fetch_path = os.path.dirname(os.path.realpath(__file__))+"/fetch/"
    key_name = instance["KeyName"]
    key_path = os.path.dirname(os.path.realpath(__file__))+"/keys/"+key_name+".pem"
    command = ["ssh", "-i", key_path, GLOBAL_CONFIG["aws_username"]+"@"+instance["PublicIpAddress"], "ls", "-ll", "~/lobot/fetch"]
    if os.path.exists(key_path):
        print("Output of \"ls -ll ~/lobot/fetch\" on remote machine:")
        return_code = subprocess.call(command)
        if return_code == 2:
            return_code = subprocess.call(["ssh", "-i", key_path, GLOBAL_CONFIG["aws_username"]+"@"+instance["PublicIpAddress"], "mkdir", "~/lobot", ";", "mkdir", "~/lobot/fetch"])
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
    chosen_confirmation = prompt.prompt(confirm_prompt)["fetch"]
    if chosen_confirmation:
        if not os.path.exists(fetch_path):
            print("No \"fetch\" folder in the script's directory \""+os.path.dirname(os.path.realpath(__file__)))
            return
        command = ["scp", "-i", key_path, "-r", GLOBAL_CONFIG["aws_username"]+"@"+instance["PublicIpAddress"]+":lobot/fetch/", fetch_path]
        if os.path.exists(key_path):
            subprocess.call(command)
        else:
            raise ValueError("Key"+key_name+".pem is not available in my keys folder")

def ask_instance(instances):
        sorted_list = sorted(instances, key=lambda x: x["State"])
        choices = [inst["InstanceId"]+" :: ("+inst["State"]+", "+inst["Name"]+")" for inst in sorted_list] + ["Change region", "Change username (SSH)"]
        instance_prompt = {
            'type': 'list',
            'name': 'instance',
            'message': 'Choose instance, change region, or change SSH username:',
            'choices': choices
        }
        answer = prompt.prompt(instance_prompt)['instance'].split(" :: ")[0]
        return answer

def change_region(current_region_name):
    ec2 = boto3.client("ec2")
    known_regions = [region['RegionName'] for region in ec2.describe_regions()['Regions']]
    for region_idx, region_name in enumerate(known_regions):
        try:
            location_name = REGION_TO_READABLE_NAME[region_name]
        except KeyError:
            raise KeyError("Region "+str(region_name)+" does not have a readable name. Please check https://docs.aws.amazon.com/general/latest/gr/rande.html and update the REGION_TO_READABLE_NAME dictionary")
        known_regions[region_idx] = region_name + "  -  " + location_name
    region_prompt = {
         'type': 'list',
         'name': 'region',
         'message': 'Current region: '+str(current_region_name)+'. Which region do you want instead?',
         'choices': known_regions
     }
    chosen_region = prompt.prompt(region_prompt)['region'].split("  -  ")[0]
    return chosen_region

def detailed_info(instance, region_name):
    ec2 = boto3.client("ec2", region_name=region_name)
    current_info = ec2.describe_instances(InstanceIds=[instance["InstanceId"]])["Reservations"][0]["Instances"][0]
    relevant_info = {}
    table = PrettyTable(["Key", "Value"])
    relevant_info["AMI Id"] = current_info["ImageId"]
    try:
        relevant_info["AMI Name"] = imageid_to_name(relevant_info["AMI Id"])
    except ClientError:
        print("\nAMI Id could not be mapped to name ..")
    relevant_info["Availability Zone"] = current_info["Placement"]["AvailabilityZone"]
    relevant_info["Number of CPU cores"] = current_info["CpuOptions"]["CoreCount"]
    print("")
    for info_name, info_content in relevant_info.items():
        table.add_row([info_name, info_content])
    print(table)

if __name__ == "__main__":
    GLOBAL_CONFIG = read_config()
    recommended_instance_types = read_config(os.path.dirname(os.path.realpath(__file__))+"/instance_types.cfg")
    # If not specified, takes default configured region.
    try:
        client_region_name = GLOBAL_CONFIG["aws_region"]
    except ValueError:
        client_region_name = boto3.client("ec2").meta.region_name

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
        client_region_name = GLOBAL_CONFIG["aws_region"]
        os.system("clear")
        #print("Loading instances")
        instances, used_types, client_region_name = get_current_instances(region_name=client_region_name, include_prices=GLOBAL_CONFIG["load_prices"])
        #print("\t ... done")
        display_instances(instances, region_name=client_region_name)
        time.sleep(0.5)
        # Choose instance
        chosen_instance = ask_instance(instances)
        if chosen_instance == "Change region":
            GLOBAL_CONFIG["aws_region"] = change_region(current_region_name=client_region_name)
            time.sleep(1)
            continue
        elif chosen_instance == "Change username (SSH)":
            change_remote_username()
            time.sleep(1)
            continue
        else:
            for inst in instances:
                if inst["InstanceId"] == chosen_instance:
                    chosen_instance = inst
            # Choose action
            options = []
            options.append("Details")
            instance_name = chosen_instance["Name"]
            deploy_option_name = "Deploy data to \""+str(instance_name)+"\""
            fetch_option_name= "Fetch data from \""+str(instance_name)+"\""
            if chosen_instance["State"] == "running" and chosen_instance["PublicIpAddress"] is not None:
                options.append("Open shell (SSH)")
                options.append("Jupyter")
                options.append(deploy_option_name)
                options.append(fetch_option_name)
                options.append("Change name")
                options.append("Stop")
            elif chosen_instance["State"] in ("terminated", "terminating"):
                options = ["Nothing to do here."]
            else:
                options.append("Start")
                options.append("Change name")
                options.append("Change type")
            time.sleep(2)
            chosen_action = prompt.prompt({'type':"list", "name":"action", "message": "What do you want to do?", "choices":options})["action"]
            if chosen_action == "Start":
                response = start_instance(chosen_instance, region_name=client_region_name)
            if chosen_action == "Stop":
                response = stop_instance(chosen_instance, region_name=client_region_name)
            if chosen_action == "Open shell (SSH)":
                connect_instance(chosen_instance)
            if chosen_action == "Jupyter":
                process = start_jupyter(chosen_instance)
            if chosen_action == "Kill Jupyters":
                kill_jupyters(chosen_instance)
            if chosen_action == "Change type":
                change_type(chosen_instance, region_name=client_region_name, available_instances=recommended_instance_types)
            if chosen_action == "Change name":
                change_name(chosen_instance, region_name=client_region_name)
            if chosen_action == deploy_option_name:
                deploy(chosen_instance)
            if chosen_action == fetch_option_name:
                fetch(chosen_instance)
            if chosen_action == "Details":
                detailed_info(chosen_instance, region_name=client_region_name)
        time.sleep(0.5)
        input("\n\nENTER to reload script ..")
