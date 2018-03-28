# coding=utf-8
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

    @staticmethod
    def get_pod_name(job_id):
        return "digits-%s" % job_id

    def get_pods_status(self, pod_label):
        """
        根据job_id获取pod状态信息
        :param job_id:
        :return:
        """

        resp = self.v1.list_namespaced_pod(namespace='default', label_selector='app=%s' % pod_label)
        status_list = []
        for i in resp.items:
            status_list.append(i.status.phase)
        return status_list

    @staticmethod
    def generate_yaml(job_dir, pod_label, node_count):

        digits_path = os.path.dirname(digits.__file__)
        yaml_path = os.path.join(digits_path, 'tools/k8s/mpi_node_base.yaml')
        new_yaml_path = os.path.join(job_dir, 'mpi-nodes.yaml')
        name = pod_label
        with open(yaml_path, mode='r+') as r_file:
            with open(new_yaml_path, 'w+') as w_file:
                for line in r_file.readlines():
                    if line.find("$name$") >= 0:
                        new_line = line.replace("$name$", name)
                        w_file.write(new_line)
                    elif line.find("$label$") >= 0:
                        new_line = line.replace("$label$", name)
                        w_file.write(new_line)
                    elif line.find("$node_count$") >= 0:
                        new_line = line.replace("$node_count$", '%d' % node_count)
                        w_file.write(new_line)
                    else:
                        w_file.write(line)
        return new_yaml_path

    def create_deployment(self, yaml_path, pod_label):
        with open(yaml_path) as f:
            dep = yaml.load(f)
            resp = client.ExtensionsV1beta1Api().create_namespaced_deployment(body=dep, namespace="default")
            self.logger.info("Deployment created. status='%s'" % str(resp.status))
        running = False
        while not running:
            time.sleep(1)
            status = self.get_pods_status(pod_label=pod_label)
            if len(status) > 0:
                self.logger.info(status)
                running = True
                for stat in status:
                    if not stat == 'Running':
                        running = False
                        break
        self.logger.info("Deployment created successfully!")

    @staticmethod
    def delete_deployment(name):
        # Delete deployment
        api_response = client.ExtensionsV1beta1Api().delete_namespaced_deployment(
            name=name,
            namespace="default",
            body=client.V1DeleteOptions(
                propagation_policy='Foreground',
                grace_period_seconds=5))
        print("Deployment deleted. status='%s'" % str(api_response.status))

    def generate_hostfile(self, pod_label, slots, job_dir):
        resp = self.v1.list_namespaced_pod(namespace='default', label_selector='app=%s' % pod_label)
        lines = []
        for i in resp.items:
            lines.append('%s slots=%d'% (i.status.pod_ip, slots))
        hostfile_path = os.path.join(job_dir, 'hostfile')
        f = open(hostfile_path, 'w')
        lines = [line + '\n' for line in lines]
        f.writelines(lines)
        f.close()

    def container_prepare(self, job_id, job_dir='/home/nfsdir/nfsdir/zyf', node_count=1, slots=1):
        pod_label = self.get_pod_name(job_id)
        yaml_path = self.generate_yaml(job_dir=job_dir, pod_label=pod_label, node_count=node_count)
        self.create_deployment(yaml_path, pod_label=pod_label)
        self.generate_hostfile(pod_label=pod_label, slots=slots, job_dir=job_dir)

    @staticmethod
    def delete_pods(job_id):
        KubernetasCoord.delete_deployment(name=KubernetasCoord.get_pod_name(job_id))

