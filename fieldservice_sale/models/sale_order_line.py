# Copyright (C) 2019 Brian McMaster
# Copyright (C) 2019 Open Source Integrators
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo import _, api, fields, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    qty_delivered_method = fields.Selection(
        selection_add=[("field_service", "Field Service Order")],
        ondelete={"field_service": "cascade"},
    )
    fsm_order_id = fields.Many2one(
        "fsm.order",
        "Order",
        index=True,
        help="Field Service Order generated by the sales order item",
    )

    @api.depends("product_id.type")
    def _compute_product_updatable(self):
        for line in self:
            if line.product_id.type == "service" and line.state == "sale":
                line.product_updatable = False
            else:
                return super(SaleOrderLine, line)._compute_product_updatable()

    @api.depends("product_id")
    def _compute_qty_delivered_method(self):
        res = super(SaleOrderLine, self)._compute_qty_delivered_method()
        for line in self:
            if not line.is_expense and line.product_id.field_service_tracking == "line":
                line.qty_delivered_method = "field_service"
        return res

    @api.depends("fsm_order_id.stage_id")
    def _compute_qty_delivered(self):
        res = super(SaleOrderLine, self)._compute_qty_delivered()
        lines_by_fsm = self.filtered(
            lambda sol: sol.qty_delivered_method == "field_service"
        )
        complete = self.env.ref("fieldservice.fsm_stage_completed")
        for line in lines_by_fsm:
            qty = 0
            if line.fsm_order_id.stage_id == complete:
                qty = line.product_uom_qty
                line.qty_delivered = qty
        return res

    @api.model_create_multi
    def create(self, vals_list):
        lines = super(SaleOrderLine, self).create(vals_list)
        for line in lines:
            if line.state == "sale":
                line._field_service_generation()
        return line

    def _field_create_fsm_order_prepare_values(self):
        self.ensure_one()
        categories = self.product_id.fsm_order_template_id.category_ids
        return {
            "location_id": self.order_id.fsm_location_id.id,
            "location_directions": self.order_id.fsm_location_id.direction,
            "request_early": self.order_id.expected_date,
            "scheduled_date_start": self.order_id.expected_date,
            "description": self.name,
            "template_id": self.product_id.fsm_order_template_id.id,
            "todo": self.product_id.fsm_order_template_id.instructions,
            "category_ids": [(6, 0, categories.ids)],
            "scheduled_duration": self.product_id.fsm_order_template_id.duration,
            "sale_id": self.order_id.id,
            "sale_line_id": self.id,
            "company_id": self.company_id.id,
        }

    def _field_create_fsm_order(self):
        """Generate fsm_order for the given so line, and link it.
        :return a mapping with the so line id and its linked fsm_order
        :rtype dict
        """
        result = {}
        for so_line in self:
            # create fsm_order
            values = so_line._field_create_fsm_order_prepare_values()
            fsm_order = self.env["fsm.order"].sudo().create(values)
            so_line.fsm_order_id = fsm_order.id
            # post message on SO
            msg_body = _(
                """Field Service Order Created ({}): <a href=
                   # data-oe-model=fsm.order data-oe-id={}>{}</a>
                """
            ).format(so_line.product_id.name, fsm_order.id, fsm_order.name)
            so_line.order_id.message_post(body=msg_body)
            # post message on fsm_order
            fsm_order_msg = _(
                """This order has been created from: <a href=
                   # data-oe-model=sale.order data-oe-id={}>{}</a> ({})
                """
            ).format(
                so_line.order_id.id, so_line.order_id.name, so_line.product_id.name
            )
            fsm_order.message_post(body=fsm_order_msg)
            result[so_line.id] = fsm_order
        return result

    def _field_find_fsm_order(self):
        """Find the fsm_order generated by the so lines. If no fsm_order
        linked, it will be created automatically.
        :return a mapping with the so line id and its linked fsm_order
        :rtype dict
        """
        for rec in self:
            # one search for all so lines
            fsm_orders = self.env["fsm.order"].search([("sale_line_id", "in", rec.ids)])
            fsm_order_sol_mapping = {
                fsm_order.sale_line_id.id: fsm_order for fsm_order in fsm_orders
            }
            res = {}
            for so_line in rec:
                # If the SO was confirmed, cancelled, set to draft then confirmed,
                # avoid creating a new fsm_order.
                fsm_order = fsm_order_sol_mapping.get(so_line.id)
                # If not found, create one fsm_order for the so line
                if not fsm_order:
                    fsm_order = so_line._field_create_fsm_order()[so_line.id]
                res[so_line.id] = fsm_order
            return res

    def _field_service_generation(self):
        """For service lines, create the field service order. If it already
        exists, it simply links the existing one to the line.
        """
        for rec in self:
            if rec.product_id.field_service_tracking == "sale":
                sale = rec.order_id
                so_fo_mapping = sale._field_find_fsm_order()
                rec.fsm_order_id = so_fo_mapping[rec.order_id.id].id
            if rec.product_id.field_service_tracking == "line":
                rec._field_find_fsm_order()

    def _prepare_invoice_line(self, **optional_values):
        res = super()._prepare_invoice_line(**optional_values)
        if self.fsm_order_id:
            res.update(
                {
                    "fsm_order_ids": [(4, self.fsm_order_id.id)],
                }
            )
        return res
