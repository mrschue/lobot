# lobot
Lobot is a cloud helper for **EC2 by Amazon Web Services (AWS)**, written for Python 3.

It provides an interactive CLI to conveniently manage your Linux-based EC2 instances and perform actions such as
* Start instance
* Stop instance
* Change instance type
* Open SSH
* Start and connect to a Jupyter notebook server
* Change instance's 'Name'-tag
* Display other instance details

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
You might want to make your old key inactive.

Once you have it, execute the following instruction in a terminal and follow the instructions:
```
aws configure
``` 

