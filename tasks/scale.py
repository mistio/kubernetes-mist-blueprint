import os

from cloudify import ctx
from cloudify.state import ctx_parameters as params

from plugin import constants
from plugin.utils import LocalStorage
from plugin.utils import generate_name
from plugin.utils import get_stack_name
from plugin.utils import wait_for_event

from plugin.server import create_machine
from plugin.connection import MistConnectionClient


if __name__ == '__main__':
    # FIXME HACK
    storage = LocalStorage()
    storage.copy_node_instance(ctx.instance.id)

    # FIXME Re-think this.
    conn = MistConnectionClient()
    ctx.instance.runtime_properties['job_id'] = conn.client.job_id

    # Create a copy of the node's immutable properties in order to update them.
    node_properties = ctx.node.properties.copy()

    # Override the node's properties with parameters passed from workflows.
    for key in params:
        if key in node_properties['parameters']:
            node_properties['parameters'][key] = params[key]
            ctx.logger.info('Added %s=%s to node properties', key, params[key])

    # Generate a somewhat random machine name. NOTE that we need the name at
    # this early point in order to be passed into cloud-init, if used, so that
    # we may use it later on to match log entries.
    name = generate_name(get_stack_name(), 'worker')
    node_properties['parameters']['name'] = name
    ctx.instance.runtime_properties['machine_name'] = name

    # Generate cloud-init, if supported.
    if conn.cloud.provider in constants.CLOUD_INIT_PROVIDERS:
        cloud_init = os.path.join(os.path.dirname(__file__), 'cloud_init.yml')
        ctx.download_resource_and_render(
            os.path.join('cloud-init', 'cloud-init.yml'), cloud_init
        )
        with open(os.path.abspath(cloud_init)) as fobj:
            cloud_init = fobj.read()
        node_properties['parameters']['cloud_init'] = cloud_init
        ctx.instance.runtime_properties['cloud_init'] = cloud_init

    # Create the nodes. Get the master node's IP address. NOTE that we prefer
    # to use private IP addresses.
    create_machine(node_properties, node_type='worker')

    # Wait for machine creation to finish, before moving to configuration step.
    if conn.cloud.provider in constants.CLOUD_INIT_PROVIDERS:
        wait_for_event(
            job_id=ctx.instance.runtime_properties['job_id'],
            job_kwargs={
                'action': 'cloud_init_finished',
                'machine_name': ctx.instance.runtime_properties['machine_name']
            }
        )
    elif not ctx.node.properties['configured']:
        ctx.logger.info('Configuring kubernetes node')

        # Prepare script parameters.
        params = "-m '%s' " % ctx.instance.runtime_properties['master_ip']
        params += "-t '%s' " % ctx.instance.runtime_properties['master_token']
        params += "-r 'node'"

        # Run the script.
        script = conn.client.run_script(
            script_id=ctx.instance.runtime_properties['script_id'], su=True,
            machine_id=ctx.instance.runtime_properties['machine_id'],
            cloud_id=ctx.instance.runtime_properties['cloud_id'],
            script_params=params,
        )
        wait_for_event(
            job_id=ctx.instance.runtime_properties['job_id'],
            job_kwargs={
                'action': 'script_finished',
                'machine_id': ctx.instance.runtime_properties['machine_id'],
            }
        )
        ctx.logger.info('Kubernetes installation succeeded!')
    else:
        ctx.logger.info('Kubernetes already configured')
