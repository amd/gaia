"""
Acme Corp REST API Reference
=============================

This module documents the Acme Corp REST API v2.0.

Authentication
--------------
All API endpoints require authentication. The API uses Bearer token authentication.
Clients must include a valid token in the Authorization header of every request.

    Authorization: Bearer <your_token_here>

Tokens are issued via the /auth/token endpoint and expire after 24 hours.
To obtain a token, POST your API key and secret to /auth/token.

Rate Limiting
-------------
Requests are limited to 1000 per hour per token. Exceeding this limit returns HTTP 429.

Base URL
--------
Production:  https://api.acmecorp.com/v2
Staging:     https://api-staging.acmecorp.com/v2
"""

from typing import Optional
import requests


BASE_URL = "https://api.acmecorp.com/v2"


def get_auth_token(api_key: str, api_secret: str) -> dict:
    """
    Obtain a Bearer token for API authentication.

    All subsequent API calls must include this token in the Authorization header:
        Authorization: Bearer <token>

    Authentication uses Bearer token via the Authorization header.

    Args:
        api_key (str): Your Acme Corp API key (found in the developer portal).
        api_secret (str): Your Acme Corp API secret.

    Returns:
        dict: A dictionary containing:
            - token (str): The Bearer token string.
            - expires_at (str): ISO 8601 timestamp when the token expires.
            - token_type (str): Always "Bearer".

    Raises:
        requests.HTTPError: If credentials are invalid (HTTP 401).
        requests.ConnectionError: If the API server is unreachable.

    Example usage::

        >>> result = get_auth_token("my-api-key", "my-api-secret")
        >>> print(result["token"])
        'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...'
        >>> print(result["token_type"])
        'Bearer'

        # Use the token in subsequent calls:
        >>> headers = {"Authorization": f"Bearer {result['token']}"}
        >>> response = requests.get(f"{BASE_URL}/products", headers=headers)
    """
    response = requests.post(
        f"{BASE_URL}/auth/token",
        json={"api_key": api_key, "api_secret": api_secret},
    )
    response.raise_for_status()
    return response.json()


def list_products(
    token: str,
    category: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """
    Retrieve a paginated list of products from the Acme Corp catalog.

    Requires authentication: pass the Bearer token in the Authorization header.

    Args:
        token (str): Bearer token obtained from get_auth_token().
        category (str, optional): Filter by product category (e.g., "widgets", "gadgets").
            If None, returns products across all categories.
        page (int): Page number for pagination. Defaults to 1.
        per_page (int): Number of results per page (max 100). Defaults to 20.

    Returns:
        dict: A dictionary containing:
            - items (list): List of product objects, each with id, name, price, and category.
            - total (int): Total number of matching products.
            - page (int): Current page number.
            - pages (int): Total number of pages.

    Raises:
        requests.HTTPError: If authentication fails (HTTP 401) or the request is malformed.

    Example usage::

        >>> token = get_auth_token("key", "secret")["token"]
        >>> result = list_products(token, category="widgets", per_page=50)
        >>> print(f"Found {result['total']} widgets across {result['pages']} pages")
        Found 142 widgets across 3 pages
        >>> for product in result["items"]:
        ...     print(f"  {product['name']}: ${product['price']}")
        Widget Pro X: $199.99
        Widget Basic: $49.99
    """
    headers = {"Authorization": f"Bearer {token}"}
    params = {"page": page, "per_page": per_page}
    if category:
        params["category"] = category

    response = requests.get(f"{BASE_URL}/products", headers=headers, params=params)
    response.raise_for_status()
    return response.json()


def get_product(token: str, product_id: str) -> dict:
    """
    Retrieve details for a single product by its ID.

    Args:
        token (str): Bearer token for authorization. Must be sent in the
            Authorization header as: Authorization: Bearer <token>.
        product_id (str): The unique identifier of the product (e.g., "WPX-001").

    Returns:
        dict: A product object containing:
            - id (str): Unique product identifier.
            - name (str): Product display name.
            - description (str): Full product description.
            - price (float): Unit price in USD.
            - category (str): Product category.
            - in_stock (bool): Whether the product is currently available.
            - created_at (str): ISO 8601 timestamp of product creation.

    Raises:
        requests.HTTPError: HTTP 404 if product_id not found; HTTP 401 if token invalid.

    Example usage::

        >>> token = get_auth_token("key", "secret")["token"]
        >>> product = get_product(token, "WPX-001")
        >>> print(product["name"])
        'Widget Pro X'
        >>> print(product["price"])
        199.99
        >>> print(product["in_stock"])
        True
    """
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(f"{BASE_URL}/products/{product_id}", headers=headers)
    response.raise_for_status()
    return response.json()


def create_order(
    token: str,
    items: list[dict],
    shipping_address: dict,
    notes: Optional[str] = None,
) -> dict:
    """
    Create a new order in the Acme Corp system.

    This endpoint submits an order for the specified items and shipping address.
    Requires a valid Bearer token in the Authorization header.

    Args:
        token (str): Bearer token from get_auth_token(). Used as:
            Authorization: Bearer <token>
        items (list[dict]): List of order items. Each item must contain:
            - product_id (str): Product identifier.
            - quantity (int): Number of units to order.
        shipping_address (dict): Delivery address containing:
            - name (str): Recipient name.
            - street (str): Street address.
            - city (str): City name.
            - state (str): Two-letter state code.
            - zip (str): ZIP/postal code.
            - country (str): ISO 3166-1 alpha-2 country code (e.g., "US").
        notes (str, optional): Special instructions for the order. Max 500 characters.

    Returns:
        dict: Order confirmation containing:
            - order_id (str): Unique order identifier (e.g., "ORD-20250315-8842").
            - status (str): Initial order status, typically "pending".
            - total (float): Order total in USD, including tax and shipping.
            - estimated_delivery (str): ISO 8601 estimated delivery date.

    Raises:
        requests.HTTPError: HTTP 400 if items list is empty or product IDs are invalid;
            HTTP 402 if payment method on file is declined; HTTP 401 if token is expired.

    Example usage::

        >>> token = get_auth_token("key", "secret")["token"]
        >>> order = create_order(
        ...     token=token,
        ...     items=[
        ...         {"product_id": "WPX-001", "quantity": 5},
        ...         {"product_id": "GP-002", "quantity": 2},
        ...     ],
        ...     shipping_address={
        ...         "name": "Sarah Chen",
        ...         "street": "123 Main St",
        ...         "city": "San Francisco",
        ...         "state": "CA",
        ...         "zip": "94105",
        ...         "country": "US",
        ...     },
        ...     notes="Please use reinforced packaging."
        ... )
        >>> print(order["order_id"])
        'ORD-20250315-8842'
        >>> print(order["status"])
        'pending'
        >>> print(f"Order total: ${order['total']:.2f}")
        Order total: $1,087.45
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "items": items,
        "shipping_address": shipping_address,
    }
    if notes:
        payload["notes"] = notes

    response = requests.post(f"{BASE_URL}/orders", headers=headers, json=payload)
    response.raise_for_status()
    return response.json()
