import os
import subprocess
import digits
import yaml
import time
from kubernetes import client, config


class KubernetasCoord(object):

    def __init__(self, logger):
        self.logger = logger
        config.load_kube_config()
        self.v1 = client.CoreV1Api()
        self.extensions_v1beta1 = client.ExtensionsV1beta1Api()

    def get_pods_status(self, job_id):
        resp = self.v1.list_namespaced_pod(namespace='default', label_selector='app=%s' % job_id)
        status_list = []
        for i in resp.items:
            status_list.append(i.status.phase)
        return status_list

    def generate_yaml(self, job_dir):
        digits_path = os.path.dirname(digits.__file__)
        yaml_path = os.path.join(digits_path, 'tools/k8s/mpi-nodes.yaml')
        return yaml_path

    def create_pods(self, yaml_path, job_id):
        with open(yaml_path) as f:
            dep = yaml.load(f)
            resp = self.extensions_v1beta1.create_namespaced_deployment(body=dep, namespace="default")
            self.logger.info("Deployment created. status='%s'" % str(resp.status))
        running = False
        while (not running):
            time.sleep(1)
            status = self.get_pods_status(job_id=job_id)
            if len(status) > 0:
                self.logger.info(status)
                running = True
                for stat in status:
                    if not stat == 'Running':
                        running = False
                        break
        self.logger.info("Deployment created successfully!")

    # def create_pods(self, yaml_path):
    #     p = subprocess.Popen(args=['kubectl', 'apply', '-f', yaml_path], stdout=subprocess.PIPE,
    #                          stderr=subprocess.STDOUT)
    #     if p.wait() == 0:
    #         self.logger.info('create pods successfully!')

    def delete_deployment(self, api_instance, name):
        # Delete deployment
        api_response = api_instance.delete_namespaced_deployment(
            name=name,
            namespace="default",
            body=client.V1DeleteOptions(
                propagation_policy='Foreground',
                grace_period_seconds=5))
        print("Deployment deleted. status='%s'" % str(api_response.status))

    def generate_hostfile(self, job_id, slots, job_dir):
        resp = self.v1.list_namespaced_pod(namespace='default', label_selector='app=%s' % job_id)
        lines = []
        for i in resp.items:
            lines.append('%s slots=%d'% (i.status.pod_ip, slots))
        hostfile_path = os.path.join(job_dir, 'hostfile')
        f = open(hostfile_path, 'w')
        lines = [line + '\n' for line in lines]
        f.writelines(lines)
        f.close()

    # def generate_hostfile(self, job_id, slots, job_dir):
    #     p1 = subprocess.Popen(args=['kubectl get po -o wide '], shell=True, stdout=subprocess.PIPE,
    #                           stderr=subprocess.STDOUT)
    #     p2 = subprocess.Popen(args=['grep', job_id], stdin=p1.stdout, stdout=subprocess.PIPE,
    #                           stderr=subprocess.STDOUT)
    #     awk_args = '{print $6, \"slots=%d\"}' % slots
    #     p3 = subprocess.Popen(args=['awk', awk_args], stdin=p2.stdout, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    #
    #     p4 = subprocess.Popen(args=['tee', os.path.join(job_dir, 'hostfile')], stdin=p3.stdout, stdout=subprocess.PIPE,
    #                           stderr=subprocess.STDOUT)
    #     self.logger.info(p4.stdout.read())

    def container_prepare(self, job_id='mpi-nodes', job_dir='/home/nfsdir/nfsdir/zyf', slots=1):

        yaml_path = self.generate_yaml(job_dir)
        self.create_pods(yaml_path, job_id=job_id)
        self.generate_hostfile(job_id=job_id, slots=slots, job_dir=job_dir)

    def delete_pods(self, job_id):
        self.delete_deployment(self.extensions_v1beta1, name=job_id)

