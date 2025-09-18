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

import boto3  # type: ignore
import logging
import sys
import traceback
import os
import json
from botocore.exceptions import ClientError
from aws_lambda_powertools import Tracer # type: ignore
from aws_lambda_powertools import Logger # type: ignore

sts_client = boto3.client('sts')
tracer = Tracer(service="code-build-proxy")
logger = Logger(service="code-build-proxy")

@tracer.capture_method
def assume_role(role_arn: str):
    """
    Function to assume an IAM Role
    
    Parameters: 
        role_arn (str): the ARN of the role to assume
    
    Returns:
        boto3 session object
    """
    try:
        logger.info(f"Assuming Role: {role_arn}")
        assumedRole = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName='cross_account_role'
        )
    except:
        logger.exception("Failed to assume role")  
        raise RuntimeError(f"Could not assume role: {role_arn}")
    return boto3.Session(
        aws_access_key_id=assumedRole['Credentials']['AccessKeyId'],
        aws_secret_access_key=assumedRole['Credentials']['SecretAccessKey'],
        aws_session_token=assumedRole['Credentials']['SessionToken'])

@tracer.capture_method
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
        logger.exception(f"Failed to start CodeBuild Project: {codebuild_project}")     
        raise RuntimeError(f"Could not start CodeBuild Project: {codebuild_project}")

    logger.info(f"Started CodeBuild Job: {codebuild_response['build']['id']}")
    return {
        'codeBuildJobId': codebuild_response['build']['id']
    }  

@tracer.capture_method
def check_codebuild_status(codebuild_session_object, codebuild_job_id):
    """
    Check the status of the supplied CodeBuild Job ID
    
    Parameters:
        codebuild_session_object: boto3 session object
        codebuild_job_id (str): the CodeBuild execution ID to check

    Returns:
        job_status (str): the build status of the CodeBuild exection
    """
    try:
        codebuild_response = codebuild_session_object.batch_get_builds(
                                ids=[codebuild_job_id]
                            )
    except:
        logger.exception(f"Failed to check job status: {codebuild_job_id}") 
        raise RuntimeError(f"Exception checking job status: {codebuild_job_id}")
    job_status = codebuild_response['builds'][0]['buildStatus']
    return job_status

@tracer.capture_method
def start_build_handler(event):
    """
    Function for start CodeBuild workflow

    Parameters:
        event (dict): the supplied Lambda event
    
    Returns:
        event (dict): The Lambda event object
    """
    if not event['roleArn']:
        raise Exception("Event did not include the roleArn")
    if not event['codeBuildProject']:
        raise Exception("Event did not include the target CodeBuild Project")
    if not event['region']:
        raise Exception("Event did not include the region for the target CodeBuild Project")
    if not event['environmentVariables']:
        event['environmentVariables'] = []

    boto3_session = assume_role(event['roleArn'])
    codebuild_client = boto3_session.client('codebuild', region_name=event['region'])

    codebuild_start = start_codebuild_project(codebuild_client, event['codeBuildProject'], event['environmentVariables'])
    logger.info(f"CodeBuild Job started successfully, ID: {codebuild_start['codeBuildJobId']}")

    # append CodeBuild Job Id to the supplied event
    event.update({"CodeBuildJobStatus": "IN_PROGRESS"})
    event.update({"CodeBuildJobId": codebuild_start['codeBuildJobId']})
    return event

@tracer.capture_method
def check_build_status_handler(event):
    """
    Function to check the status of a CodeBuild Job ID

    Parameters:
        event (dict): the supplied Lambda event
    
    Returns:
        event (dict): The Lambda event object
    """
    if not event['roleArn']:
        raise Exception("Event did not include the roleArn")
    if not event['jobId']:
        raise Exception("Event did not include the CodeBuild ID to check")
    if not event['region']:
        raise Exception("Event did not include the region for the target CodeBuild ID")

    boto3_session = assume_role(event['roleArn'])
    codebuild_client = boto3_session.client('codebuild', region_name=event['region'])

    codebuild_status = check_codebuild_status(codebuild_client, event['jobId'])
    logger.info(f"CodeBuild Job Status: {codebuild_status}")

    # append CodeBuild Job Id to the supplied event
    event.update({"CodeBuildJobStatus": codebuild_status})
    event.update({"CodeBuildJobId": event['jobId']})
    return event

@tracer.capture_lambda_handler
@logger.inject_lambda_context(log_event=True)
def lambda_handler(event, context):
    """
    Lambda handler for the remote CodeBuild Orchestration function

    This Lambda provides an interface to execute a CodeBuild project in a remote account. This 
    includes a method to start a CodeBuild project with Environment Variable overrides and a 
    method to report on the status of a project execution.

    Parameters:
        event (dict): The Lambda event object
        context (dict): The Lambda context object   
    
    Returns:
        event (dict): The updated event object
    """
    if event['invocationType'] == "START_BUILD":
        logger.info("Starting CodeBuild Project")
        return start_build_handler(event)
    if event['invocationType'] == "CHECK_STATUS":
        logger.info("Checking CodeBuild Job Status")
        return check_build_status_handler(event)