# Copyright 2016 Ucamco - Wim Audenaert <wim.audenaert@ucamco.com>
# Copyright 2016-19 Eficent Business and IT Consulting Services S.L.
# - Jordi Ballester Alomar <jordi.ballester@eficent.com>
# - Lois Rilo <lois.rilo@eficent.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

from odoo import api, fields, models, exceptions, _
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT
from datetime import date, datetime, timedelta
import logging
from odoo.tools.float_utils import float_round
logger = logging.getLogger(__name__)


class MultiLevelMrp(models.TransientModel):
    _name = 'mrp.multi.level'

    mrp_area_ids = fields.Many2many(
        comodel_name="mrp.area",
        string="MRP Areas to run",
        help="If empty, all areas will be computed.",
    )
    # ANDPIO-2019-05-28
    use_quantity_on_hand = fields.Boolean(string='Quantity On Hand', default=True)
    use_sale_order = fields.Boolean(string='Sale Order', default=True)
    use_purchase_order = fields.Boolean(string='Purchase Order', default=True)
    use_manufactoring_order = fields.Boolean(string='Manufactoring Order', default=True)
    use_manufactoring_material = fields.Boolean(string='Manufactoring Material', default=True)
    use_sale_blanket_order = fields.Boolean(string='Sale Blanket Order', default=True)
    use_purchase_blanket_order = fields.Boolean(string='Purchase Blanket Order', default=False)
    use_stock_demand_estimate = fields.Boolean(string='Compute Stock Estimates', default=False)
    use_request_for_quotation = fields.Boolean(string='Request for Quotation', default=False)
    # TODO: dates are not being correctly computed for supply...

    @api.model
    def _prepare_product_mrp_area_data(self, product_mrp_area):
        qty_available = 0.0
        if self.use_quantity_on_hand:
            product_obj = self.env['product.product']
            # TODO: move mrp_qty_available computation, maybe unreserved??
            location_ids = self.env['stock.location'].search(
                [('id', 'child_of', product_mrp_area.mrp_area_id.location_id.id)])
            for location in location_ids:
                product_l = product_obj.with_context(
                    {'location': location.id}).browse(
                    product_mrp_area.product_id.id)
                qty_available += product_l.qty_available

        return {
            'product_mrp_area_id': product_mrp_area.id,
            'mrp_qty_available': qty_available,
            'mrp_llc': product_mrp_area.product_id.llc,
        }

    @api.model
    def _prepare_mrp_move_data_from_forecast(
            self, estimate, product_mrp_area, date):
        mrp_type = 'd'
        origin = 'fc'
        daily_qty = float_round(
            estimate.daily_qty,
            precision_rounding=product_mrp_area.product_id.uom_id.rounding,
            rounding_method='HALF-UP')
        return {
            'mrp_area_id': product_mrp_area.mrp_area_id.id,
            'product_id': product_mrp_area.product_id.id,
            'product_mrp_area_id': product_mrp_area.id,
            'production_id': None,
            'purchase_order_id': None,
            'purchase_line_id': None,
            'stock_move_id': None,
            'mrp_qty': -daily_qty,
            'current_qty': -daily_qty,
            'mrp_date': date,
            'current_date': date,
            'mrp_action': 'none',
            'mrp_type': mrp_type,
            'mrp_processed': False,
            'mrp_origin': origin,
            'mrp_order_number': None,
            'parent_product_id': None,
            'running_availability': 0.00,
            'name': 'Forecast',
            'state': 'confirmed',
        }

    @api.model
    def _prepare_mrp_move_data_from_stock_move(
            self, product_mrp_area, move, direction='in', origin='??'):
        if direction == 'out':
            mrp_type = 'd'
            product_qty = -move.product_qty
        else:
            mrp_type = 's'
            product_qty = move.product_qty
        po = None
        po_line = None
        mo = None
        order_number = None
        parent_product_id = None
        # ANDPIO 20190523
        if move.sale_line_id:
            order_number = move.sale_line_id.order_id.name
            #origin = 'so'
        if move.purchase_line_id:
            order_number = move.purchase_line_id.order_id.name
            #origin = 'po'
            po = move.purchase_line_id.order_id.id
            po_line = move.purchase_line_id.id
        if move.production_id:
            order_number = move.production_id.name
            #origin = 'mo'
            mo = move.production_id.id
        else:
            # TODO: move.move_dest_id -> move.move_dest_ids. DONE, review
            if move.move_dest_ids:
                # move_dest_id = move.move_dest_ids[:1]
                for move_dest_id in move.move_dest_ids:
                    if move_dest_id.production_id:
                        order_number = move_dest_id.production_id.name
                        # ANDPIO 20190523 origin = 'mo'
                        mo = move_dest_id.production_id.id
                        if move_dest_id.production_id.product_id:
                            parent_product_id = \
                                move_dest_id.production_id.product_id.id
                        else:
                            parent_product_id = move_dest_id.product_id.id
        if order_number is None:
            order_number = move.name
        mrp_date = date.today()
        if datetime.date(datetime.strptime(
                move.date_expected,
                DEFAULT_SERVER_DATETIME_FORMAT)) > date.today():
            mrp_date = datetime.date(datetime.strptime(
                move.date_expected, DEFAULT_SERVER_DATETIME_FORMAT))
        return {
            'product_id': move.product_id.id,
            'product_mrp_area_id': product_mrp_area.id,
            'production_id': mo,
            'purchase_order_id': po,
            'purchase_line_id': po_line,
            'stock_move_id': move.id,
            'mrp_qty': product_qty,
            'current_qty': product_qty,
            'mrp_date': mrp_date,
            'current_date': move.date_expected,
            'mrp_action': 'none',
            'mrp_type': mrp_type,
            'mrp_processed': False,
            'mrp_origin': origin,
            'mrp_order_number': order_number,
            'parent_product_id': parent_product_id,
            'running_availability': 0.00,
            'name': order_number,
            'state': move.state,
        }

    @api.model
    def _prepare_mrp_move_data_supply(
            self, product_mrp_area, qty, mrp_date_supply, mrp_action_date,
            mrp_action, name):
        return {
            'product_id': product_mrp_area.product_id.id,
            'product_mrp_area_id': product_mrp_area.id,
            'production_id': None,
            'purchase_order_id': None,
            'purchase_line_id': None,
            'stock_move_id': None,
            'mrp_qty': qty,
            'current_qty': None,
            'mrp_date': mrp_date_supply,
            'mrp_action_date': mrp_action_date,
            'current_date': None,
            'mrp_action': mrp_action,
            'mrp_type': 's',
            'mrp_processed': False,
            'mrp_origin': None,
            'mrp_order_number': None,
            'parent_product_id': None,
            'name': 'Supply: ' + name,
        }

    @api.model
    def _prepare_mrp_move_data_bom_explosion(
            self, product, bomline, qty, mrp_date_demand_2, bom, name):
        product_mrp_area = self._get_product_mrp_area_from_product_and_area(
            bomline.product_id, product.mrp_area_id)
        if not product_mrp_area:
            raise exceptions.Warning(
                _("No MRP product found"))

        return {
            'mrp_area_id': product.mrp_area_id.id,
            'product_id': bomline.product_id.id,
            'product_mrp_area_id': product_mrp_area.id,
            'production_id': None,
            'purchase_order_id': None,
            'purchase_line_id': None,
            'stock_move_id': None,
            'mrp_qty': -(qty * bomline.product_qty),  # TODO: review with UoM
            'current_qty': None,
            'mrp_date': mrp_date_demand_2,
            'current_date': None,
            'mrp_action': 'none',
            'mrp_type': 'd',
            'mrp_processed': False,
            'mrp_origin': 'mrp',
            'mrp_order_number': None,
            'parent_product_id': bom.product_id.id,
            'name':
                ('Demand Bom Explosion: ' + name).replace(
                    'Demand Bom Explosion: Demand Bom '
                    'Explosion: ',
                    'Demand Bom Explosion: '),
        }

    @api.model
    def create_move(self, product_mrp_area_id, mrp_date, mrp_qty, name):
        self = self.with_context(auditlog_disabled=True)

        values = {}
        if not isinstance(mrp_date, date):
            mrp_date = fields.Date.from_string(mrp_date)

        if product_mrp_area_id.supply_method == 'buy':
            # if product_mrp_area_id.purchase_requisition:
            #     mrp_action = 'pr'
            # else:
            mrp_action = 'po'
        else:
            # TODO: consider 'none'...
            mrp_action = 'mo'

        if mrp_date < date.today():
            mrp_date_supply = date.today()
        else:
            mrp_date_supply = mrp_date

        calendar = product_mrp_area_id.mrp_area_id.calendar_id
        if calendar and product_mrp_area_id.mrp_lead_time:
            date_str = fields.Date.to_string(mrp_date)
            dt = fields.Datetime.from_string(date_str)
            res = calendar.plan_days(
                -1 * product_mrp_area_id.mrp_lead_time - 1, dt)
            mrp_action_date = res.date()
        else:
            mrp_action_date = mrp_date - timedelta(
                days=product_mrp_area_id.mrp_lead_time)

        qty_ordered = 0.00
        qty_to_order = mrp_qty
        while qty_ordered < mrp_qty:
            qty = product_mrp_area_id._adjust_qty_to_order(qty_to_order)
            qty_to_order -= qty
            move_data = self._prepare_mrp_move_data_supply(
                product_mrp_area_id, qty, mrp_date_supply, mrp_action_date,
                mrp_action, name)
            mrpmove_id = self.env['mrp.move'].create(move_data)
            qty_ordered = qty_ordered + qty

            if mrp_action == 'mo':
                mrp_date_demand = mrp_action_date
                if mrp_date_demand < date.today():
                    mrp_date_demand = date.today()
                if not product_mrp_area_id.product_id.bom_ids:
                    continue
                bomcount = 0
                for bom in product_mrp_area_id.product_id.bom_ids:
                    if not bom.active or not bom.bom_line_ids:
                        continue
                    bomcount += 1
                    if bomcount != 1:
                        continue
                    for bomline in bom.bom_line_ids:
                        if bomline.product_qty <= 0.00 or \
                                bomline.product_id.type != 'product':
                            continue
                        if self._exclude_from_mrp(
                                bomline.product_id,
                                product_mrp_area_id.mrp_area_id):
                            # Stop explosion.
                            continue
                        # TODO: review: mrp_transit_delay, mrp_inspection_delay
                        mrp_date_demand_2 = mrp_date_demand - timedelta(
                            days=(product_mrp_area_id.mrp_transit_delay +
                                  product_mrp_area_id.mrp_inspection_delay))
                        move_data = \
                            self._prepare_mrp_move_data_bom_explosion(
                                product_mrp_area_id, bomline, qty,
                                mrp_date_demand_2,
                                bom, name)
                        mrpmove_id2 = self.env['mrp.move'].create(move_data)
                        mrpmove_id.mrp_move_down_ids = [(4, mrpmove_id2.id)]
        values['qty_ordered'] = qty_ordered
        log_msg = '[%s] %s: qty_ordered = %s' % (
            product_mrp_area_id.mrp_area_id.name,
            product_mrp_area_id.product_id.default_code or
            product_mrp_area_id.product_id.name,
            qty_ordered,
        )
        logger.debug(log_msg)
        return values

    @api.model
    def _mrp_cleanup(self, mrp_areas):
        logger.info('Start MRP Cleanup')
        domain = []
        if mrp_areas:
            domain += [('mrp_area_id', 'in', mrp_areas.ids)]
        self.env['mrp.move'].search(domain).unlink()
        self.env['mrp.inventory'].search(domain).unlink()
        logger.info('End MRP Cleanup')
        return True

    @api.model
    def _low_level_code_calculation(self):
        logger.info('Start low level code calculation')
        counter = 999999
        llc = 0
        self.env['product.product'].search([]).write({'llc': llc})
        products = self.env['product.product'].search([('llc', '=', llc)])
        if products:
            counter = len(products)
        log_msg = 'Low level code 0 finished - Nbr. products: %s' % counter
        logger.info(log_msg)

        while counter:
            llc += 1
            products = self.env['product.product'].search(
                [('llc', '=', llc - 1)])
            p_templates = products.mapped('product_tmpl_id')
            bom_lines = self.env['mrp.bom.line'].search(
                [('product_id.llc', '=', llc - 1),
                 ('bom_id.product_tmpl_id', 'in', p_templates.ids)])
            products = bom_lines.mapped('product_id')
            products.write({'llc': llc})
            products = self.env['product.product'].search([('llc', '=', llc)])
            counter = len(products)
            log_msg = 'Low level code %s finished - Nbr. products: %s' % (
                llc, counter)
            logger.info(log_msg)

        mrp_lowest_llc = llc
        logger.info('End low level code calculation')
        return mrp_lowest_llc

    # applicable: ad hoc rule
    @api.model
    def _adjust_mrp_applicable(self, mrp_areas):
        """This method is meant to modify the products that are applicable
           to MRP Multi level calculation
        """
        return True

    # applicable: type=product
    @api.model
    def _calculate_mrp_applicable(self, mrp_areas):
        logger.info('Start Calculate MRP Applicable')
        domain = []
        if mrp_areas:
            domain += [('mrp_area_id', 'in', mrp_areas.ids)]
        self.env['product.mrp.area'].search(domain).write(
            {'mrp_applicable': False})
        domain += [('product_id.type', '=', 'product')]
        self.env['product.mrp.area'].search(domain).write(
            {'mrp_applicable': True})
        self._adjust_mrp_applicable(mrp_areas)
        count_domain = [('mrp_applicable', '=', True)]
        if mrp_areas:
            count_domain += [('mrp_area_id', 'in', mrp_areas.ids)]
        counter = self.env['product.mrp.area'].search(count_domain, count=True)
        log_msg = 'End Calculate MRP Applicable: %s' % counter
        logger.info(log_msg)
        return True

    # init quantity on hand (use in _prepare_mrp_product_data)
    @api.model
    def _init_mrp_move_from_forecast(self, product_mrp_area):
        locations = self.env['stock.location'].search(
            [('id', 'child_of', product_mrp_area.mrp_area_id.location_id.id)])
        today = fields.Date.today()
        estimates = self.env['stock.demand.estimate'].search([
            ('product_id', '=', product_mrp_area.product_id.id),
            ('location_id', 'in', locations.ids),
            ('date_range_id.date_end', '>=', today)
        ])
        for rec in estimates:
            start = rec.date_range_id.date_start
            if start < today:
                start = today
            mrp_date = fields.Date.from_string(start)
            date_end = fields.Date.from_string(rec.date_range_id.date_end)
            delta = timedelta(days=1)
            while mrp_date <= date_end:
                mrp_move_data = \
                    self._prepare_mrp_move_data_from_forecast(
                        rec, product_mrp_area, mrp_date)
                self.env['mrp.move'].create(mrp_move_data)
                mrp_date += delta
        return True

    # TODO: move this methods to product_mrp_area?? to be able to
    # show moves with an action
    @api.model
    def _in_stock_moves_domain(self, product_mrp_area):
        locations = self.env['stock.location'].search(
            [('id', 'child_of', product_mrp_area.mrp_area_id.location_id.id)])
        return [
            ('product_id', '=', product_mrp_area.product_id.id),
            ('state', 'not in', ['done', 'cancel']),
            ('product_qty', '>', 0.00),
            ('location_id', 'not in', locations.ids),
            ('location_dest_id', 'in', locations.ids),
        ]

    @api.model
    def _out_stock_moves_domain(self, product_mrp_area):
        locations = self.env['stock.location'].search(
            [('id', 'child_of', product_mrp_area.mrp_area_id.location_id.id)])
        return [
            ('product_id', '=', product_mrp_area.product_id.id),
            ('state', 'not in', ['done', 'cancel']),
            ('product_qty', '>', 0.00),
            ('location_id', 'in', locations.ids),
            ('location_dest_id', 'not in', locations.ids),
        ]

    # init move (use in use_purchase_order, use_manufactoring_order, use_manufactoring_material, use_sale_order)
    @api.model
    def _init_mrp_move_from_stock_move(self, product_mrp_area):
        # TODO: Should we exclude the quantity done from the moves?
        move_obj = self.env['stock.move']
        mrp_move_obj = self.env['mrp.move']
        in_domain = self._in_stock_moves_domain(product_mrp_area)
        in_moves = move_obj.search(in_domain)
        out_domain = self._out_stock_moves_domain(product_mrp_area)
        out_moves = move_obj.search(out_domain)
        if in_moves:
            for move in in_moves:
                if move.purchase_line_id and self.use_purchase_order:
                    move_data = self._prepare_mrp_move_data_from_stock_move(
                        product_mrp_area, move, direction='in', origin='po')
                    mrp_move_obj.create(move_data)
                elif move.production_id and self.use_manufactoring_order:
                    move_data = self._prepare_mrp_move_data_from_stock_move(
                        product_mrp_area, move, direction='in', origin='mo')
                    mrp_move_obj.create(move_data)
                #else:
                #    move_data = self._prepare_mrp_move_data_from_stock_move(
                #        product_mrp_area, move, direction='in', origin='??')
                #    mrp_move_obj.create(move_data)
        if out_moves:
            for move in out_moves:
                if move.raw_material_production_id and self.use_manufactoring_material:
                    move_data = self._prepare_mrp_move_data_from_stock_move(
                        product_mrp_area, move, direction='out', origin='mm')
                    mrp_move_obj.create(move_data)
                elif move.sale_line_id and self.use_sale_order:
                    move_data = self._prepare_mrp_move_data_from_stock_move(
                        product_mrp_area, move, direction='out', origin='so')
                    mrp_move_obj.create(move_data)
                #else:
                #    move_data = self._prepare_mrp_move_data_from_stock_move(
                #        product_mrp_area, move, direction='out', origin='??')
                #    mrp_move_obj.create(move_data)
        return True

    @api.model
    def _prepare_mrp_move_data_from_purchase_order(
            self, poline, product_mrp_area):
        mrp_date = date.today()
        if fields.Date.from_string(poline.date_planned) > date.today():
            mrp_date = fields.Date.from_string(poline.date_planned)
        return {
            'product_id': poline.product_id.id,
            'product_mrp_area_id': product_mrp_area.id,
            'production_id': None,
            'purchase_order_id': poline.order_id.id,
            'purchase_line_id': poline.id,
            'stock_move_id': None,
            'mrp_qty': poline.product_qty,
            'current_qty': poline.product_qty,
            'mrp_date': mrp_date,
            'current_date': poline.date_planned,
            'mrp_action': 'none',
            'mrp_type': 's',
            'mrp_processed': False,
            'mrp_origin': 'po',
            'mrp_order_number': poline.order_id.name,
            'parent_product_id': None,
            'running_availability': 0.00,
            'name': poline.order_id.name,
            'state': poline.order_id.state,
        }

    @api.model
    def _init_mrp_move_from_purchase_order(self, product_mrp_area):
        location_ids = self.env['stock.location'].search(
            [('id', 'child_of', product_mrp_area.mrp_area_id.location_id.id)])
        picking_types = self.env['stock.picking.type'].search(
            [('default_location_dest_id', 'in',
              location_ids.ids)])
        picking_type_ids = [ptype.id for ptype in picking_types]
        orders = self.env['purchase.order'].search(
            [('picking_type_id', 'in', picking_type_ids),
             ('state', 'in', ['draft', 'sent', 'to approve'])])
        po_lines = self.env['purchase.order.line'].search(
            [('order_id', 'in', orders.ids),
             ('product_qty', '>', 0.0),
             ('product_id', '=', product_mrp_area.product_id.id)])

        for line in po_lines:
            mrp_move_data = \
                self._prepare_mrp_move_data_from_purchase_order(
                    line, product_mrp_area)
            self.env['mrp.move'].create(mrp_move_data)

    # ANDPIO 2019-05-21
    @api.model
    def _prepare_mrp_move_data_from_sale_blanket_order(
        self, sboline, product_mrp_area):
        mrp_date = date.today()
        if fields.Date.from_string(sboline.date_schedule) > date.today():
            mrp_date = fields.Date.from_string(sboline.date_schedule)
        return {
            'product_id': sboline.product_id.id,
            'product_mrp_area_id': product_mrp_area.id,
            'production_id': None,
            'purchase_order_id': None,
            'purchase_line_id': None,
            'stock_move_id': None,
            'mrp_qty': -sboline.remaining_qty,
            'current_qty': -sboline.remaining_qty,
            'mrp_date': mrp_date,
            'current_date': sboline.date_schedule,
            'mrp_action': 'none',
            'mrp_type': 'd',
            'mrp_processed': False,
            'mrp_origin': 'sb',
            'mrp_order_number': sboline.order_id.name,
            'parent_product_id': None,
            'running_availability': 0.00,
            'name': sboline.order_id.name,
            'state': sboline.order_id.state,
        }

    @api.model
    def _init_mrp_move_from_sale_blanket_order(self, product_mrp_area):
        sbo_orders = self.env['sale.blanket.order'].search(
            [('confirmed', '=', True),
             ('state', '=', 'open')])
        sbo_lines = self.env['sale.blanket.order.line'].search(
            [('order_id', 'in', sbo_orders.ids),
             ('remaining_qty', '>', 0.0),
             ('product_id', '=', product_mrp_area.product_id.id)])
        for line in sbo_lines:
            mrp_move_data = self._prepare_mrp_move_data_from_sale_blanket_order(
                line, product_mrp_area)
            self.env['mrp.move'].create(mrp_move_data)

    # ANDPIO 2019-05-21
    @api.model
    def _prepare_mrp_move_data_from_purchase_blanket_order(
        self, pboline, product_mrp_area):
        mrp_date = date.today()
        if fields.Date.from_string(pboline.date_schedule) > date.today():
            mrp_date = fields.Date.from_string(pboline.date_schedule)
        return {
            'product_id': pboline.product_id.id,
            'product_mrp_area_id': product_mrp_area.id,
            'production_id': None,
            'purchase_order_id': None,
            'purchase_line_id': None,
            'stock_move_id': None,
            'mrp_qty': pboline.product_qty,
            'current_qty': pboline.product_qty,
            'mrp_date': mrp_date,
            'current_date': pboline.date_schedule,
            'mrp_action': 'none',
            'mrp_type': 's',
            'mrp_processed': False,
            'mrp_origin': 'pb',
            'mrp_order_number': pboline.requisition_id.name,
            'parent_product_id': None,
            'running_availability': 0.00,
            'name': pboline.requisition_id.name,
            'state': pboline.requisition_id.state,
        }

    @api.model
    def _init_mrp_move_from_purchase_blanket_order(self, product_mrp_area):
        pbo_orders = self.env['purchase.requisition'].search(
            [('state', 'in', ['in_progress', 'open'])])
        pbo_lines = self.env['purchase.requisition.line'].search(
            [('requisition_id', 'in', pbo_orders.ids),
             ('product_qty', '>', 0.0),
             ('product_id', '=', product_mrp_area.product_id.id)])
        for line in pbo_lines:
            mrp_move_data = self._prepare_mrp_move_data_from_purchase_blanket_order(
                line, product_mrp_area)
            self.env['mrp.move'].create(mrp_move_data)

    @api.model
    def _get_product_mrp_area_from_product_and_area(self, product, mrp_area):
        return self.env['product.mrp.area'].search([
            ('product_id', '=', product.id),
            ('mrp_area_id', '=', mrp_area.id),
        ], limit=1)

    @api.model
    def _init_mrp_move(self, product_mrp_area):
        if self.use_stock_demand_estimate:
            self._init_mrp_move_from_forecast(product_mrp_area)
        self._init_mrp_move_from_stock_move(product_mrp_area)
        if self.use_request_for_quotation:
            self._init_mrp_move_from_purchase_order(product_mrp_area)
        if self.use_sale_blanket_order:
            self._init_mrp_move_from_sale_blanket_order(product_mrp_area)
        if self.use_purchase_blanket_order:
            self._init_mrp_move_from_purchase_blanket_order(product_mrp_area)

    @api.model
    def _exclude_from_mrp(self, product, mrp_area):
        """ To extend with various logic where needed. """
        product_mrp_area = self.env['product.mrp.area'].search(
            [('product_id', '=', product.id),
             ('mrp_area_id', '=', mrp_area.id)], limit=1)
        if not product_mrp_area:
            return True
        return product_mrp_area.mrp_exclude

    @api.model
    def _mrp_initialisation(self, mrp_areas):
        logger.info('Start MRP initialisation')
        if not mrp_areas:
            mrp_areas = self.env['mrp.area'].search([])
        product_mrp_areas = self.env['product.mrp.area'].search([
            ('mrp_area_id', 'in', mrp_areas.ids),
            ('mrp_applicable', '=', True),
        ])
        init_counter = 0
        for mrp_area in mrp_areas:
            for product_mrp_area in product_mrp_areas.filtered(
                    lambda a: a.mrp_area_id == mrp_area):
                if self._exclude_from_mrp(
                        product_mrp_area.product_id, mrp_area):
                    continue
                init_counter += 1
                log_msg = 'MRP Init: %s - %s ' % (
                    init_counter, product_mrp_area.display_name)
                logger.info(log_msg)
                self._init_mrp_move(product_mrp_area)
        logger.info('End MRP initialisation')

    @api.model
    def _init_mrp_move_grouped_demand(self, nbr_create, product_mrp_area):
        last_date = None
        last_qty = 0.00
        # ANDPIO-2019-05-28
        if self.use_quantity_on_hand:
            onhand = product_mrp_area.qty_available
        else:
            onhand = 0.0
        grouping_delta = product_mrp_area.mrp_nbr_days
        for move in product_mrp_area.mrp_move_ids.filtered(
                lambda m: m.mrp_action == 'none'):
            if last_date and (
                    fields.Date.from_string(move.mrp_date)
                    >= last_date + timedelta(days=grouping_delta)) and (
                        (onhand + last_qty + move.mrp_qty)
                        < product_mrp_area.mrp_minimum_stock
                        or (onhand + last_qty)
                        < product_mrp_area.mrp_minimum_stock):
                name = 'Grouped Demand for %d Days' % grouping_delta
                qtytoorder = product_mrp_area.mrp_minimum_stock - last_qty
                cm = self.create_move(
                    product_mrp_area_id=product_mrp_area,
                    mrp_date=last_date,
                    mrp_qty=qtytoorder,
                    name=name)
                qty_ordered = cm.get('qty_ordered', 0.0)
                onhand = onhand + last_qty + qty_ordered
                last_date = None
                last_qty = 0.00
                nbr_create += 1
            if (onhand + last_qty + move.mrp_qty) < \
                    product_mrp_area.mrp_minimum_stock or \
                    (onhand + last_qty) < \
                    product_mrp_area.mrp_minimum_stock:
                if not last_date:
                    last_date = fields.Date.from_string(move.mrp_date)
                    last_qty = move.mrp_qty
                else:
                    last_qty += move.mrp_qty
            else:
                last_date = fields.Date.from_string(move.mrp_date)
                onhand += move.mrp_qty

        if last_date and last_qty != 0.00:
            name = 'Grouped Demand for %d Days' % grouping_delta
            qtytoorder = product_mrp_area.mrp_minimum_stock - onhand - last_qty
            cm = self.create_move(
                product_mrp_area_id=product_mrp_area, mrp_date=last_date,
                mrp_qty=qtytoorder, name=name)
            qty_ordered = cm.get('qty_ordered', 0.0)
            onhand += qty_ordered
            nbr_create += 1
        return nbr_create

    @api.model
    def _mrp_calculation(self, mrp_lowest_llc, mrp_areas):
        logger.info('Start MRP calculation')
        product_mrp_area_obj = self.env['product.mrp.area']
        counter = 0
        if not mrp_areas:
            mrp_areas = self.env['mrp.area'].search([])
        for mrp_area in mrp_areas:
            llc = 0
            while mrp_lowest_llc > llc:
                product_mrp_areas = product_mrp_area_obj.search(
                    [('product_id.llc', '=', llc),
                     ('mrp_area_id', '=', mrp_area.id)])
                llc += 1

                for product_mrp_area in product_mrp_areas:
                    nbr_create = 0
                    # ANDPIO-2019-05-28
                    if self.use_quantity_on_hand:
                        onhand = product_mrp_area.qty_available
                    else:
                        onhand = 0.0
                    # TODO: unreserved?
                    if product_mrp_area.mrp_nbr_days == 0:
                        # todo: review ordering by date
                        for move in product_mrp_area.mrp_move_ids:
                            if move.mrp_action == 'none':
                                if (onhand + move.mrp_qty) < \
                                        product_mrp_area.mrp_minimum_stock:
                                    qtytoorder = \
                                        product_mrp_area.mrp_minimum_stock - \
                                        onhand - move.mrp_qty
                                    cm = self.create_move(
                                        product_mrp_area_id=product_mrp_area,
                                        mrp_date=move.mrp_date,
                                        mrp_qty=qtytoorder, name=move.name)
                                    qty_ordered = cm['qty_ordered']
                                    onhand += move.mrp_qty + qty_ordered
                                    nbr_create += 1
                                else:
                                    onhand += move.mrp_qty
                    else:
                        nbr_create = self._init_mrp_move_grouped_demand(
                            nbr_create, product_mrp_area)

                    if onhand < product_mrp_area.mrp_minimum_stock and \
                            nbr_create == 0:
                        qtytoorder = \
                            product_mrp_area.mrp_minimum_stock - onhand
                        cm = self.create_move(
                            product_mrp_area_id=product_mrp_area,
                            mrp_date=date.today(),
                            mrp_qty=qtytoorder,
                            name='Minimum Stock')
                        qty_ordered = cm['qty_ordered']
                        onhand += qty_ordered
                    counter += 1

            log_msg = 'MRP Calculation LLC %s Finished - Nbr. products: %s' % (
                llc - 1, counter)
            logger.info(log_msg)

        logger.info('Enb MRP calculation')

    @api.model
    def _get_demand_groups(self, product_mrp_area):
        query = """
            SELECT mrp_date, sum(mrp_qty)
            FROM mrp_move
            WHERE product_mrp_area_id = %(mrp_product)s
            AND mrp_type = 'd'
            GROUP BY mrp_date
        """
        params = {
            'mrp_product': product_mrp_area.id
        }
        return query, params

    @api.model
    def _get_supply_groups(self, product_mrp_area):
        query = """
                SELECT mrp_date, sum(mrp_qty)
                FROM mrp_move
                WHERE product_mrp_area_id = %(mrp_product)s
                AND mrp_type = 's'
                AND mrp_action = 'none'
                GROUP BY mrp_date
            """
        params = {
            'mrp_product': product_mrp_area.id
        }
        return query, params

    @api.model
    def _get_supply_action_groups(self, product_mrp_area):
        exclude_mrp_actions = ['none', 'cancel']
        query = """
                SELECT mrp_date, sum(mrp_qty)
                FROM mrp_move
                WHERE product_mrp_area_id = %(mrp_product)s
                AND mrp_qty <> 0.0
                AND mrp_type = 's'
                AND mrp_action not in %(excluded_mrp_actions)s
                GROUP BY mrp_date
            """
        params = {
            'mrp_product': product_mrp_area.id,
            'excluded_mrp_actions': tuple(exclude_mrp_actions,)
        }
        return query, params

    @api.model
    def _init_mrp_inventory(self, product_mrp_area):
        mrp_move_obj = self.env['mrp.move']
        # Read Demand
        demand_qty_by_date = {}
        query, params = self._get_demand_groups(product_mrp_area)
        self.env.cr.execute(query, params)
        for mrp_date, qty in self.env.cr.fetchall():
            demand_qty_by_date[mrp_date] = qty
        # Read Supply
        supply_qty_by_date = {}
        query, params = self._get_supply_groups(product_mrp_area)
        self.env.cr.execute(query, params)
        for mrp_date, qty in self.env.cr.fetchall():
            supply_qty_by_date[mrp_date] = qty
        # Read supply actions
        # TODO: if we remove cancel take it into account here,
        # TODO: as well as mrp_type ('r').
        supply_actions_qty_by_date = {}
        query, params = self._get_supply_action_groups(product_mrp_area)
        self.env.cr.execute(query, params)
        for mrp_date, qty in self.env.cr.fetchall():
            supply_actions_qty_by_date[mrp_date] = qty
        # Dates
        mrp_dates = set(mrp_move_obj.search([
            ('product_mrp_area_id', '=', product_mrp_area.id)],
            order='mrp_date').mapped('mrp_date'))
        on_hand_qty = product_mrp_area.product_id.with_context(
            location=product_mrp_area.mrp_area_id.location_id.id
        )._product_available()[
            product_mrp_area.product_id.id]['qty_available']
        # ANDPIO-2019-05-28
        if not self.use_quantity_on_hand:
            on_hand_qty = 0.0
        # TODO: unreserved?
        for mdt in sorted(mrp_dates):
            mrp_inventory_data = {
                'product_mrp_area_id': product_mrp_area.id,
                'date': mdt,
            }
            demand_qty = 0.0
            supply_qty = 0.0
            if mdt in demand_qty_by_date.keys():
                demand_qty = demand_qty_by_date[mdt]
                mrp_inventory_data['demand_qty'] = abs(demand_qty)
            if mdt in supply_qty_by_date.keys():
                supply_qty = supply_qty_by_date[mdt]
                mrp_inventory_data['supply_qty'] = abs(supply_qty)
            if mdt in supply_actions_qty_by_date.keys():
                mrp_inventory_data['to_procure'] = \
                    supply_actions_qty_by_date[mdt]
            mrp_inventory_data['initial_on_hand_qty'] = on_hand_qty
            on_hand_qty += (supply_qty + demand_qty)
            mrp_inventory_data['final_on_hand_qty'] = on_hand_qty

            self.env['mrp.inventory'].create(mrp_inventory_data)

    @api.model
    def _mrp_final_process(self, mrp_areas):
        logger.info('Start MRP final process')
        domain = [('product_id.llc', '<', 9999)]
        if mrp_areas:
            domain += [('mrp_area_id', 'in', mrp_areas.ids)]
        product_mrp_area_ids = self.env['product.mrp.area'].search(domain)

        for product_mrp_area in product_mrp_area_ids:
            # Build the time-phased inventory
            self._init_mrp_inventory(product_mrp_area)

            # Complete info on mrp_move (running availability and nbr actions)
            # ANDPIO-2019-05-28
            if self.use_quantity_on_hand:
                qoh = product_mrp_area.qty_available
            else:
                qoh = 0

            moves = self.env['mrp.move'].search([
                ('product_mrp_area_id', '=', product_mrp_area.id)],
                order='mrp_date, mrp_type desc, id')
            for move in moves:
                qoh = qoh + move.mrp_qty
                move.running_availability = qoh
        logger.info('End MRP final process')

    @api.multi
    def run_mrp_multi_level(self):
        # cleanup
        self._mrp_cleanup(self.mrp_area_ids)
        # low level code
        mrp_lowest_llc = self._low_level_code_calculation()
        # applicable 
        self._calculate_mrp_applicable(self.mrp_area_ids)
        # init
        self._mrp_initialisation(self.mrp_area_ids)
        # mrp
        self._mrp_calculation(mrp_lowest_llc, self.mrp_area_ids)
        # procure
        self._mrp_final_process(self.mrp_area_ids)
