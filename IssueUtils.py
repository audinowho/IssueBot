import json
import requests
import jwt
import time



# https://gist.github.com/pelson/47c0c89a3522ed8da5cc305afc2562b0
def create_bearer_token_header(private_key, app_id):
    time_since_epoch_in_seconds = int(time.time())

    payload = {
        # issued at time
        # steupid Daylight Savings Time.  Have to turn it back an hour to be valid.
        'iat': time_since_epoch_in_seconds,
        # JWT expiration time (10 minute maximum)
        'exp': time_since_epoch_in_seconds + (10 * 60),
        # GitHub App's identifier
        'iss': app_id
    }

    actual_jwt = jwt.encode(payload, private_key, algorithm='RS256')

    headers = {"Authorization": "Bearer {}".format(actual_jwt),
               "Accept": "application/vnd.github.machine-man-preview+json"}
    return headers

def get_access_token_header(private_key, app_id, install_id):

    # https://github.community/t/how-to-get-github-app-installation-id-for-a-user/127276
    resp = requests.post('https://api.github.com/app/installations/{}/access_tokens'.format(install_id),
                         headers=create_bearer_token_header(private_key, app_id))
    resp.raise_for_status()

    resp_json = json.loads(resp.content.decode())

    headers = {"Authorization": "token {}".format(resp_json["token"]),
               "Accept": "application/vnd.github.machine-man-preview+json"}
    return headers

def create_issue(headers, repo_owner, repo_name, title, body, labels):
    url = 'https://api.github.com/repos/%s/%s/issues' % (repo_owner, repo_name)
    issue = {'title': title,
             'body': body,
             'labels': labels}
    resp = requests.post(url, json=issue, headers=headers)
    resp.raise_for_status()

    resp_json = json.loads(resp.content.decode())
    issue_url = resp_json["url"]
    return issue_url

def add_issue_label(headers, repo_owner, repo_name, issue_id, labels):
    url = 'https://api.github.com/repos/%s/%s/issues/%s/labels' % (repo_owner, repo_name, issue_id)
    resp = requests.post(url, json=labels, headers=headers)
    resp.raise_for_status()

    resp_json = json.loads(resp.content.decode())
    #issue_url = resp_json["url"]

def request_app(private_key, app_id):

    resp = requests.get('https://api.github.com/app', headers=create_bearer_token_header(private_key, app_id))
    resp.raise_for_status()

    print('Code: ', resp.status_code)
    print('Content: ', resp.content.decode())

#request_app(cert_str, APP_ID)
#header = get_access_token_header(cert_str, APP_ID, INSTALL_ID)
#create_issue(header, REPO_OWNER, REPO_NAME, "Test", "Test Issue")
#add_issue_label(header, REPO_OWNER, REPO_NAME, "20", ["test2"])

