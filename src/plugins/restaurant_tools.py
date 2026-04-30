"""
Restaurant tools for Venero restaurant order management system.
Provides mock functions for order processing, price conversion, address validation, and delivery time calculation.
"""

from typing import Any, Dict, List
from datetime import datetime, timedelta
import re


# Mock menu data
MENU = {
    "بيتزا": {"price": 150, "category": "main"},
    "pizza": {"price": 150, "category": "main"},
    "الفراخ المشوية": {"price": 120, "category": "main"},
    "chicken": {"price": 120, "category": "main"},
    "السلطة الخضراء": {"price": 50, "category": "salad"},
    "salad": {"price": 50, "category": "salad"},
    "الشوربة": {"price": 35, "category": "soup"},
    "soup": {"price": 35, "category": "soup"},
    "المشروبات": {"price": 20, "category": "drinks"},
    "drinks": {"price": 20, "category": "drinks"},
    "العصائر": {"price": 25, "category": "drinks"},
    "juices": {"price": 25, "category": "drinks"},
    "الحلويات": {"price": 40, "category": "desserts"},
    "desserts": {"price": 40, "category": "desserts"},
}

# Currency exchange rate (mock)
USD_TO_EGP_RATE = 30.5


async def process_order(
    items: List[Dict[str, Any]],
    quantities: List[int],
    customer_name: str = "",
) -> Dict[str, Any]:
    """
    Process a restaurant order.
    
    Args:
        items: List of menu items (names or IDs)
        quantities: Corresponding quantities for each item
        customer_name: Name of the customer
        
    Returns:
        Dictionary with order details including items, quantities, and total price in EGP
    """
    order_items = []
    total_price = 0.0
    
    for item, quantity in zip(items, quantities):
        item_lower = item.lower().strip()
        
        # Try to find the item in the menu
        menu_item = None
        for menu_key, menu_value in MENU.items():
            if menu_key.lower() == item_lower:
                menu_item = menu_value
                break
        
        if menu_item:
            item_price = menu_item["price"]
            item_total = item_price * quantity
            total_price += item_total
            
            order_items.append({
                "name": item,
                "quantity": quantity,
                "unit_price": item_price,
                "total": item_total,
                "currency": "EGP"
            })
        else:
            return {
                "status": "error",
                "message": f"العنصر '{item}' غير موجود في قائمة الطعام",
                "available_items": list(MENU.keys())
            }
    
    order_id = f"VENERO-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    return {
        "status": "success",
        "order_id": order_id,
        "customer_name": customer_name,
        "items": order_items,
        "subtotal": total_price,
        "delivery_fee": 15.0,  # Fixed delivery fee
        "total": total_price + 15.0,
        "currency": "EGP",
        "timestamp": datetime.now().isoformat()
    }


async def convert_price_to_egp(
    amount: float,
    currency: str = "USD"
) -> Dict[str, Any]:
    """
    Convert price to Egyptian Pounds (EGP).
    
    Args:
        amount: Price amount
        currency: Currency code (USD, EUR, etc.)
        
    Returns:
        Dictionary with conversion details
    """
    if currency.upper() == "EGP":
        return {
            "original_amount": amount,
            "original_currency": currency,
            "converted_amount": amount,
            "converted_currency": "EGP",
            "exchange_rate": 1.0,
            "status": "success"
        }
    elif currency.upper() == "USD":
        converted = amount * USD_TO_EGP_RATE
        return {
            "original_amount": amount,
            "original_currency": currency,
            "converted_amount": round(converted, 2),
            "converted_currency": "EGP",
            "exchange_rate": USD_TO_EGP_RATE,
            "status": "success"
        }
    else:
        # Default conversion using mock rate
        mock_rate = {
            "EUR": 33.5,
            "GBP": 38.5,
            "SAR": 8.1,
            "AED": 8.3,
        }
        rate = mock_rate.get(currency.upper(), USD_TO_EGP_RATE)
        converted = amount * rate
        return {
            "original_amount": amount,
            "original_currency": currency,
            "converted_amount": round(converted, 2),
            "converted_currency": "EGP",
            "exchange_rate": rate,
            "status": "success"
        }


async def validate_delivery_address(
    address: str,
    phone_number: str
) -> Dict[str, Any]:
    """
    Validate delivery address and phone number.
    
    Args:
        address: Delivery address
        phone_number: Customer phone number
        
    Returns:
        Dictionary with validation results
    """
    # Mock validation - check if address and phone are not empty
    address_valid = bool(address and address.strip())
    
    # Phone validation - check if it's a valid Egyptian phone format
    # Egyptian numbers typically start with +20 or 0, followed by 10 digits
    phone_pattern = r"^(?:\+20|0)?[0-9]{10,11}$"
    phone_valid = bool(re.match(phone_pattern, phone_number.replace(" ", "")))
    
    if not address_valid or not phone_valid:
        return {
            "status": "error",
            "message": "العنوان أو رقم الهاتف غير صحيح",
            "address_valid": address_valid,
            "phone_valid": phone_valid
        }
    
    # Mock address standardization
    standardized_address = address.strip()
    standardized_phone = phone_number.replace(" ", "").replace("-", "")
    
    return {
        "status": "success",
        "address": standardized_address,
        "phone_number": standardized_phone,
        "address_valid": True,
        "phone_valid": True,
        "delivery_area": "Cairo",  # Mock area
        "estimated_delivery_time": "30-45 دقيقة"
    }


async def calculate_delivery_time(
    order_id: str,
    distance_km: float = 5.0,
    rush_hour: bool = False
) -> Dict[str, Any]:
    """
    Calculate estimated delivery time for an order.
    
    Args:
        order_id: Order ID
        distance_km: Distance in kilometers (default 5km)
        rush_hour: Whether it's rush hour
        
    Returns:
        Dictionary with delivery time estimation
    """
    # Base preparation time (15 minutes)
    prep_time = 15
    
    # Delivery time calculation: ~5 min per km + 10 min
    delivery_time = int((distance_km / 5) * 15) + 10
    
    # Add extra time during rush hours
    if rush_hour:
        prep_time += 10
        delivery_time += 10
    
    total_time = prep_time + delivery_time
    
    # Calculate estimated delivery datetime
    now = datetime.now()
    estimated_delivery = now + timedelta(minutes=total_time)
    
    return {
        "status": "success",
        "order_id": order_id,
        "preparation_time": f"{prep_time} دقيقة",
        "delivery_time": f"{delivery_time} دقيقة",
        "total_estimated_time": f"{total_time} دقيقة",
        "estimated_delivery_time": estimated_delivery.strftime("%H:%M"),
        "estimated_delivery_date": estimated_delivery.strftime("%d/%m/%Y"),
        "rush_hour": rush_hour,
        "note": "سيتم التسليم في الوقت المقدر بإذن الله"
    }


async def get_menu_items() -> Dict[str, Any]:
    """
    Get all available menu items with prices.
    
    Returns:
        Dictionary with menu items and prices in EGP
    """
    menu_items = []
    for item_name, item_data in MENU.items():
        menu_items.append({
            "name": item_name,
            "price": item_data["price"],
            "category": item_data["category"],
            "currency": "EGP"
        })
    
    return {
        "status": "success",
        "restaurant": "Venero",
        "menu_items": menu_items,
        "currency": "EGP"
    }


async def get_order_status(order_id: str) -> Dict[str, Any]:
    """
    Get status of an existing order (mock implementation).
    
    Args:
        order_id: Order ID to check
        
    Returns:
        Dictionary with order status
    """
    return {
        "status": "success",
        "order_id": order_id,
        "current_status": "In Preparation",
        "status_ar": "قيد التحضير",
        "estimated_delivery": "في الطريق إليك",
        "message": "سيصل طلبك قريباً جداً"
    }
