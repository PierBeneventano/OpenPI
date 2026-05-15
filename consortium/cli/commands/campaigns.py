"""Machine-readable campaign inspection commands."""

from __future__ import annotations

import json

import click

from msc_sdk.campaigns import CampaignClient


def _emit_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, sort_keys=True))


@click.group()
@click.option("--root", type=click.Path(), default=".", help="Directory used to resolve campaign refs.")
@click.pass_context
def campaigns(ctx: click.Context, root: str) -> None:
    """Inspect local campaign bundles and observed state."""
    ctx.obj = ctx.obj or {}
    ctx.obj["campaign_root"] = root


@campaigns.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_list(ctx: click.Context, as_json: bool) -> None:
    """List local campaigns."""
    data = {"ok": True, "campaigns": CampaignClient(ctx.obj["campaign_root"]).list()}
    if as_json:
        _emit_json(data)
        return
    for campaign in data["campaigns"]:
        click.echo(campaign["name"])


@campaigns.command("create")
@click.option("--title", required=True, help="Campaign title.")
@click.option("--objective", required=True, help="Research objective.")
@click.option("--template", default="consortium_scaffold", show_default=True, help="Campaign template.")
@click.option("--budget", type=float, default=1.0, show_default=True, help="Campaign budget cap in USD.")
@click.option("--tier", default="budget", show_default=True, help="Default model tier.")
@click.option("--output-format", default="markdown", show_default=True, help="Default output format.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_create(
    ctx: click.Context,
    title: str,
    objective: str,
    template: str,
    budget: float,
    tier: str,
    output_format: str,
    as_json: bool,
) -> None:
    """Create a local-first campaign in the project campaign store."""
    data = CampaignClient(ctx.obj["campaign_root"]).create(
        title=title,
        objective=objective,
        template=template,
        budget=budget,
        tier=tier,
        output_format=output_format,
    )
    if as_json:
        _emit_json({"ok": True, "campaign": data})
        return
    click.echo(f"{data['campaign_id']}: {data['status']}")


@campaigns.command("delete")
@click.argument("campaign_ref")
@click.option("--confirm", required=True, help="Must be DELETE to confirm destructive deletion.")
@click.option("--keep-files", is_flag=True, help="Delete campaign records but leave bundle/results files in place.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_delete(ctx: click.Context, campaign_ref: str, confirm: str, keep_files: bool, as_json: bool) -> None:
    """Delete a campaign and, by default, its local bundle/results files."""
    if confirm != "DELETE":
        raise click.ClickException("Refusing to delete campaign without --confirm DELETE.")
    data = CampaignClient(ctx.obj["campaign_root"]).delete(campaign_ref, delete_files=not keep_files)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: deleted")


@campaigns.command("import")
@click.argument("bundle_path")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_import(ctx: click.Context, bundle_path: str, as_json: bool) -> None:
    """Import a JSON campaign bundle into the local campaign store."""
    data = CampaignClient(ctx.obj["campaign_root"]).import_bundle(bundle_path)
    if as_json:
        _emit_json({"ok": True, "campaign": data})
        return
    click.echo(f"{data['campaign_id']}: imported")


@campaigns.command("export")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_export(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """Export a local-first campaign to campaigns/<id>/ as a JSON bundle."""
    data = CampaignClient(ctx.obj["campaign_root"]).export_bundle(campaign_ref)
    if as_json:
        _emit_json(data)
        return
    click.echo(data["bundle_path"])


@campaigns.command("events")
@click.argument("campaign_ref")
@click.option("--limit", type=int, default=None, help="Optional number of events to return.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_events(ctx: click.Context, campaign_ref: str, limit: int | None, as_json: bool) -> None:
    """List local campaign events."""
    data = CampaignClient(ctx.obj["campaign_root"]).events(campaign_ref, limit=limit)
    if as_json:
        _emit_json(data)
        return
    for event in data["events"]:
        click.echo(f"{event['created_at']} {event['type']}")


@campaigns.command("feedback")
@click.argument("campaign_ref")
@click.option("--text", required=True, help="Human feedback to append to the campaign event stream.")
@click.option("--node", "node_id", default=None, help="Optional graph node this feedback targets.")
@click.option("--artifact-id", default=None, help="Optional artifact id this feedback targets.")
@click.option("--artifact-path", default=None, help="Optional artifact path this feedback targets.")
@click.option("--decision-id", default=None, help="Optional decision id this feedback targets.")
@click.option("--run-id", default=None, help="Optional run id this feedback targets.")
@click.option("--type", "feedback_type", default="feedback", show_default=True, help="Feedback category.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_feedback(
    ctx: click.Context,
    campaign_ref: str,
    text: str,
    node_id: str | None,
    artifact_id: str | None,
    artifact_path: str | None,
    decision_id: str | None,
    run_id: str | None,
    feedback_type: str,
    as_json: bool,
) -> None:
    """Append auditable human feedback without mutating artifacts."""
    data = CampaignClient(ctx.obj["campaign_root"]).feedback(
        campaign_ref,
        text=text,
        node_id=node_id,
        artifact_id=artifact_id,
        artifact_path=artifact_path,
        decision_id=decision_id,
        run_id=run_id,
        feedback_type=feedback_type,
    )
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: feedback recorded")


@campaigns.group("context")
def campaigns_context() -> None:
    """Manage durable campaign context links for agent chat."""


@campaigns_context.command("link")
@click.argument("campaign_ref")
@click.option("--note", required=True, help="Researcher note or concern to attach.")
@click.option("--scope", "target_scope", default="campaign", show_default=True, help="Target scope: campaign, stage, artifact, or decision.")
@click.option("--node", "node_id", default=None, help="Optional graph node target.")
@click.option("--artifact-id", default=None, help="Optional artifact id target.")
@click.option("--artifact-path", default=None, help="Optional artifact path target.")
@click.option("--decision-id", default=None, help="Optional decision id target.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_context_link(
    ctx: click.Context,
    campaign_ref: str,
    note: str,
    target_scope: str,
    node_id: str | None,
    artifact_id: str | None,
    artifact_path: str | None,
    decision_id: str | None,
    as_json: bool,
) -> None:
    """Attach durable context to a campaign, stage, artifact, or decision."""
    data = CampaignClient(ctx.obj["campaign_root"]).link_context(
        campaign_ref,
        note=note,
        target_scope=target_scope,
        node_id=node_id,
        artifact_id=artifact_id,
        artifact_path=artifact_path,
        decision_id=decision_id,
    )
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: context {data['link_id']} linked")


@campaigns_context.command("list")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_context_list(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """List durable context links for a campaign."""
    data = CampaignClient(ctx.obj["campaign_root"]).list_context_links(campaign_ref)
    if as_json:
        _emit_json(data)
        return
    for link in data["links"]:
        click.echo(f"{link['id']} {link['status']} {link['target']['scope']}: {link['note']}")


@campaigns_context.command("update")
@click.argument("campaign_ref")
@click.argument("link_id")
@click.option("--status", required=True, type=click.Choice(["active", "resolved", "superseded", "ignored"]), help="New context link status.")
@click.option("--note", default=None, help="Optional replacement or follow-up note.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_context_update(
    ctx: click.Context,
    campaign_ref: str,
    link_id: str,
    status: str,
    note: str | None,
    as_json: bool,
) -> None:
    """Update context link status."""
    data = CampaignClient(ctx.obj["campaign_root"]).update_context_link(campaign_ref, link_id, status=status, note=note)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: context {link_id} {status}")


@campaigns.command("approve-graph")
@click.argument("campaign_ref")
@click.option("--graph-version", type=int, required=True, help="Graph version to approve.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_approve_graph(
    ctx: click.Context,
    campaign_ref: str,
    graph_version: int,
    as_json: bool,
) -> None:
    """Approve a planned campaign graph version."""
    data = CampaignClient(ctx.obj["campaign_root"]).approve_graph(campaign_ref, graph_version)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']} graph {data['graph_version']}: {data['state']}")


@campaigns.command("inspect")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_inspect(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """Inspect a campaign."""
    data = CampaignClient(ctx.obj["campaign_root"]).inspect(campaign_ref).to_dict()
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: {data['status']}")


@campaigns.command("status")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_status(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """Inspect normalized campaign status."""
    data = CampaignClient(ctx.obj["campaign_root"]).status(campaign_ref)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign']}: {data['status']}")


@campaigns.command("workspace")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_workspace(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """Return the campaign workspace read model for product UIs."""
    data = CampaignClient(ctx.obj["campaign_root"]).workspace(campaign_ref)
    if as_json:
        _emit_json(data)
        return
    campaign = data["campaign"]
    execution = data["execution"]
    click.echo(f"{campaign['id']}: {execution['status']}")


@campaigns.command("graph")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_graph(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """Return a stage graph for dashboards."""
    data = CampaignClient(ctx.obj["campaign_root"]).graph(campaign_ref)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{len(data['nodes'])} nodes, {len(data['edges'])} edges")


@campaigns.command("artifacts")
@click.argument("campaign_ref")
@click.option("--stage-id", default=None, help="Optional stage id filter.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_artifacts(
    ctx: click.Context,
    campaign_ref: str,
    stage_id: str | None,
    as_json: bool,
) -> None:
    """Return declared campaign artifact contracts."""
    data = CampaignClient(ctx.obj["campaign_root"]).artifacts(campaign_ref, stage_id)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign']}: {len(data['stages'])} stages")


@campaigns.command("explain-node")
@click.argument("campaign_ref")
@click.argument("node_id")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_explain_node(ctx: click.Context, campaign_ref: str, node_id: str, as_json: bool) -> None:
    """Explain a campaign graph node and its runtime contract."""
    data = CampaignClient(ctx.obj["campaign_root"]).explain_node(campaign_ref, node_id)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['node']['id']}: {data['contract'].get('purpose') or 'no purpose'}")


@campaigns.command("propose-graph-change")
@click.argument("campaign_ref")
@click.option("--change-type", required=True, help="Change type, e.g. reroute, skip_optional_node, budget_change.")
@click.option("--instruction", required=True, help="Natural-language change instruction.")
@click.option("--node", "node_id", default=None, help="Optional node id this proposal targets.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_propose_graph_change(
    ctx: click.Context,
    campaign_ref: str,
    change_type: str,
    instruction: str,
    node_id: str | None,
    as_json: bool,
) -> None:
    """Create an auditable proposal for a graph/policy change."""
    data = CampaignClient(ctx.obj["campaign_root"]).propose_graph_change(
        campaign_ref,
        change_type=change_type,
        instruction=instruction,
        node_id=node_id,
    )
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: approval {data['approval']['id']} pending")


@campaigns.command("approve")
@click.argument("approval_id")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_approve(ctx: click.Context, approval_id: str, as_json: bool) -> None:
    """Approve a pending campaign decision."""
    data = CampaignClient(ctx.obj["campaign_root"]).approve(approval_id)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{approval_id}: approved")


@campaigns.command("reject")
@click.argument("approval_id")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_reject(ctx: click.Context, approval_id: str, as_json: bool) -> None:
    """Reject a pending campaign decision."""
    data = CampaignClient(ctx.obj["campaign_root"]).reject(approval_id)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{approval_id}: rejected")


@campaigns.command("pause")
@click.argument("campaign_ref")
@click.option("--reason", default="", help="Optional pause reason.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_pause(ctx: click.Context, campaign_ref: str, reason: str, as_json: bool) -> None:
    """Pause a campaign and record the decision."""
    data = CampaignClient(ctx.obj["campaign_root"]).pause(campaign_ref, reason=reason)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: paused")


@campaigns.command("resume")
@click.argument("campaign_ref")
@click.option("--reason", default="", help="Optional resume reason.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_resume(ctx: click.Context, campaign_ref: str, reason: str, as_json: bool) -> None:
    """Resume a paused campaign after human decision."""
    data = CampaignClient(ctx.obj["campaign_root"]).resume(campaign_ref, reason=reason)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: resumed")


@campaigns.command("stop")
@click.argument("campaign_ref")
@click.option("--reason", default="", help="Optional stop reason.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_stop(ctx: click.Context, campaign_ref: str, reason: str, as_json: bool) -> None:
    """Stop a campaign and record the decision."""
    data = CampaignClient(ctx.obj["campaign_root"]).stop(campaign_ref, reason=reason)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: stopped")


@campaigns.command("reroute")
@click.argument("campaign_ref")
@click.option("--from", "from_node", required=True, help="Source node to reroute from.")
@click.option("--to", "to_node", required=True, help="Target node to reroute to.")
@click.option("--reason", default="", help="Natural-language reroute reason.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_reroute(
    ctx: click.Context,
    campaign_ref: str,
    from_node: str,
    to_node: str,
    reason: str,
    as_json: bool,
) -> None:
    """Propose a graph reroute. This does not silently mutate the graph."""
    data = CampaignClient(ctx.obj["campaign_root"]).reroute(
        campaign_ref,
        from_node=from_node,
        to_node=to_node,
        reason=reason,
    )
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: reroute proposal pending approval")


@campaigns.command("rewrite-stage")
@click.argument("campaign_ref")
@click.argument("node_id")
@click.option("--instruction", required=True, help="Rewrite instruction for this stage.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_rewrite_stage(
    ctx: click.Context,
    campaign_ref: str,
    node_id: str,
    instruction: str,
    as_json: bool,
) -> None:
    """Propose rewriting a stage instruction."""
    data = CampaignClient(ctx.obj["campaign_root"]).rewrite_stage(campaign_ref, node_id, instruction=instruction)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: rewrite proposal pending approval")


@campaigns.command("rerun-stage")
@click.argument("campaign_ref")
@click.argument("node_id")
@click.option("--reason", default="", help="Optional rerun reason.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_rerun_stage(
    ctx: click.Context,
    campaign_ref: str,
    node_id: str,
    reason: str,
    as_json: bool,
) -> None:
    """Propose rerunning a stage."""
    data = CampaignClient(ctx.obj["campaign_root"]).rerun_stage(campaign_ref, node_id, reason=reason)
    if as_json:
        _emit_json(data)
        return
    click.echo(f"{data['campaign_id']}: rerun proposal pending approval")


@campaigns.command("summarize-artifacts")
@click.argument("campaign_ref")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def campaigns_summarize_artifacts(ctx: click.Context, campaign_ref: str, as_json: bool) -> None:
    """Summarize campaign artifact coverage and missing required artifacts."""
    data = CampaignClient(ctx.obj["campaign_root"]).summarize_artifacts(campaign_ref)
    if as_json:
        _emit_json(data)
        return
    click.echo(
        f"{data['campaign']}: {data['existing']}/{data['total']} artifacts existing, "
        f"{data['missing_required']} required missing"
    )
