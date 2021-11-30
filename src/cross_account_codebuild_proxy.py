# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this
# software and associated documentation files (the "Software"), to deal in the Software
# without restriction, including without limitation the rights to use, copy, modify,
# merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import boto3
import logging
import sys
import traceback
import os
import json
from botocore.exceptions import ClientError

sts_client = boto3.client('sts')
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def log_exception(exception_type, exception_value, exception_traceback):
    """
    Function to create a JSON object containing exception details, which can then be logged as one line to the LOGGER.
    
    Parameters:
        exception_type
        exception_value
        exception_traceback
    """
    traceback_string = traceback.format_exception(exception_type, exception_value, exception_traceback)
    err_msg = json.dumps({"errorType": exception_type.__name__, "errorMessage": str(exception_value), "stackTrace": traceback_string})
    LOGGER.error(err_msg)

def assume_role(role_arn: str):
    """
    Wrapper function to assume an IAM Role
    
    Parameters: 
        role_arn (str): the ARN of the role to assume
    """
    try:
        LOGGER.info(f"Assuming Role: {role_arn}")
        assumedRole = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName='cross_account_role'
        )
    except:
        log_exception(*sys.exc_info())    
        raise RuntimeError(f"Could not assume role: {role_arn}")
    return boto3.Session(
        aws_access_key_id=assumedRole['Credentials']['AccessKeyId'],
        aws_secret_access_key=assumedRole['Credentials']['SecretAccessKey'],
        aws_session_token=assumedRole['Credentials']['SessionToken'])


def start_codebuild_project(codebuild_session_object, codebuild_project: str, codebuild_envvars: list):
    """
    Start CodeBuild Project with environment variable overrides
    
    Parameters:
        codebuild_session_object: boto3 session object
        codebuild_project (str): The CodeBuild Project to start
        codebuile_envvars (list): List of environment variables to override on the CodeBuild execution

    Returns:
        codeBuildJobId (str): The ID of the CodeBuild execution
    """
    try:
        codebuild_response = codebuild_session_object.start_build(
                            projectName=codebuild_project,
                            environmentVariablesOverride=codebuild_envvars
                        )
    except:
        log_exception(*sys.exc_info())    
        raise RuntimeError(f"Could not start CodeBuild Project: {codebuild_project}")

    logger.info(f"Started CodeBuild Job: {codebuild_response['build']['id']}")
    return {
        'codeBuildJobId': codebuild_response['build']['id']
    }  

def check_codebuild_status(codebuild_session_object, codebuild_job_id):
    """
    Check the status of the supplied CodeBuild Job ID
    
    Parameters:
        codebuild_session_object: boto3 session object
        codebuild_job_id (str): the CodeBuild execution ID to check

    Returns:
        jobStatus (str): the execution buildStatus
    """
    try:
        codebuild_response = codebuild_session_object.batch_get_builds(
                                ids=[codebuild_job_id]
                            )
    except:
        log_exception(*sys.exc_info())    
        raise RuntimeError(f"Exception checking job status: {codebuild_job_id}")
    return {
        'jobStatus': codebuild_response['builds'][0]['buildStatus']
    }

def start_build_handler(event):
    """
    Function for start CodeBuild workflow

    Parameters:
        event (dict): the supplied Lambda event
    
    Returns: 
        event (dict): the update event object
    """
    if not event['roleArn']:
        raise Exception("Event did not include the roleArn")
    if not event['codeBuildProject']:
        raise Exception("Event did not include the target CodeBuild Project")
    if not event['environmentVariables']:
        event['environmentVariables'] = []

    boto3_session = assume_role(event['roleArn'])
    codebuild_client = boto3_session.client('codebuild')

    codebuild_start = start_codebuild_project(codebuild_client, event['codeBuildProject'], event['environmentVariables'])

    # append CodeBuild Job Id to the supplied event
    event.update({"CodeBuildJobStatus": "IN_PROGRESS"})
    event.update({"CodeBuildJobId": codebuild_start['codeBuildJobId']})
    return event

def check_build_status_handler(event):
    """
    Function to check the status of a CodeBuild Job ID

    Parameters:
        event (dict): the supplied Lambda event
    
    Returns: 
        event (dict): the update event object
    """
    if not event['roleArn']:
        raise Exception("Event did not include the roleArn")
    if not event['jobId']:
        raise Exception("Event did not include the CodeBuild ID to check")

    boto3_session = assume_role(event['roleArn'])
    codebuild_client = boto3_session.client('codebuild')

    codebuild_status = check_codebuild_status(codebuild_client, event['jobId'])

    # append CodeBuild Job Id to the supplied event
    event.update({"CodeBuildJobStatus": codebuild_status['jobStatus']})
    event.update({"CodeBuildJobId": event['jobId']})
    return event

def lambda_handler(event, context):
    """
    Lambda entry point

    Parameters:
        event (dict)
        context
    """
    if event['invocationType'] == "START_BUILD":
        return start_build_handler(event)
    if event['invocationType'] == "CHECK_STATUS":
        return check_build_status_handler(event)