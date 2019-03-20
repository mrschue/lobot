# lobot
Lobot is a cloud helper for **EC2** by **Amazon Web Services (AWS)**, written for Python 3 and meant to be used on a Linux platform.

Click for demo-video:
[![Demo](https://s3.amazonaws.com/lobot-media/low_fi.gif)](https://youtu.be/JCx_BoYGVJA)


It provides an interactive CLI to conveniently manage your Linux-based EC2 instances and perform actions such as
* Start instance
* Stop instance
* Change instance type (e.g., t3.micro -> p3.xlarge)
* Open SSH
* Start and connect to a Jupyter notebook server (needs to be installed on remote machine!)
* Change instance's 'Name'-tag
* Display other instance details (e.g., Id of used image, availability zone)

lobot does **not** provide:
* launching new instances
* terminating instances
* EBS interaction
## SETUP ##
You need the AWS CLI tools and some other dependencies. They can be conveniently installed via *pip*:
```
pip install --user awscli
pip install --user boto3
pip install --user PyInquirer
pip install --user prettytable
```

Afterwards, you'll need to get your AWS access key and secret key. You can create
one in:
```
AWS Management Console -> IAM -> Users -> %YOURNAME% -> Security credentials
```
You might want to make your old key inactive. Also, make sure that your IAM-user has appropriate access rights to EC2.

Once you have it, execute the following instruction in a terminal and follow the instructions:
```
aws configure
``` 

## USAGE ##

Just execute *lobot.py* to get an overview over your options:
```
./lobot.py
```
or 
```
python3 lobot.py
```
If you want to SSH / transfer data, the corresponding keys need to be in your *./keys* folder with 
permissions *400* (!).


The **Fetch/Deploy** options will transfer data from or to the EC2 instance. 

**Deploy:** transfers everything from the local *./deploy* folder to the remote *~/lobot/deploy* folder.

**Fetch:** transfers everything from the remote *~/lobot/fetch* folder to the local *./fetch* folder.


(*.* = folder of lobot.py and *~* = home-folder of remote user, e.g., */home/ec2-user/* on Amazon Linux)
