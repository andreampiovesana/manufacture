{
    'name': 'Multi Level MRP',
    'version': '11.0.1.0.0',
    'author': 'Ucamco, '
              'Eficent, '
              'Odoo Community Association (OCA)',
    'summary': 'Adds an MRP Scheduler',
    'website': 'https://github.com/OCA/manufacture',
    'category': 'Manufacturing',
    'depends': [
        'mrp',
        'stock',
        'purchase',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/mrp_forecast_view.xml',
        'views/mrp_area_view.xml',
        'views/product_view.xml',
        'views/stock_location_view.xml',
        'views/mrp_product_view.xml',
        'wizards/mrp_inventory_create_procurement_view.xml',
        'views/mrp_inventory_view.xml',
        'wizards/multi_level_mrp_view.xml',
        'wizards/mrp_move_create_po_view.xml',
        'views/mrp_menuitem.xml',
        'data/multi_level_mrp_cron.xml',
    ],
    'installable': True,
    'application': True,
}