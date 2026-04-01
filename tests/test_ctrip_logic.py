from __future__ import annotations

import unittest
from unittest.mock import patch

import flight_monitor


class CtripRouteParsingTests(unittest.TestCase):
    def test_parse_city_selector_remark_extracts_city_info(self) -> None:
        parsed = flight_monitor.parse_city_selector_remark("\u9009\u62e9\u57ce\u5e02[\u5317\u4eac|\u5317\u4eac(BJS)|1|BJS]")
        self.assertEqual(parsed["name"], "\u5317\u4eac")
        self.assertEqual(parsed["code"], "BJS")
        self.assertEqual(parsed["city_id"], 1)

    def test_build_ctrip_url_uses_city_codes_and_date(self) -> None:
        route = {
            "departure_city": "\u5317\u4eac",
            "arrival_city": "\u4e0a\u6d77",
            "departure_city_code": "BJS",
            "arrival_city_code": "SHA",
            "departure_date": "2026-04-15",
        }
        url = flight_monitor.build_ctrip_url(route)
        self.assertEqual(
            url,
            "https://flights.ctrip.com/online/list/oneway-bjs-sha?depdate=2026-04-15&cabin=y_s_c_f&adult=1&child=0&infant=0",
        )


class CtripPayloadParsingTests(unittest.TestCase):
    def test_parse_ctrip_flights_handles_direct_and_transfer(self) -> None:
        route = {
            'departure_city': '\u6d77\u53e3',
            'arrival_city': '\u6b66\u6c49',
            'departure_date': '2026-05-01',
        }
        payload = {
            'status': 0,
            'data': {
                'flightItineraryList': [
                    {
                        'itineraryId': 'direct-1',
                        'flightSegments': [
                            {
                                'segmentNo': 1,
                                'airlineName': '\u6d77\u5357\u822a\u7a7a',
                                'transferCount': 0,
                                'stopCount': 0,
                                'duration': 150,
                                'flightList': [
                                    {
                                        'flightNo': 'HU7063',
                                        'marketAirlineName': '\u6d77\u5357\u822a\u7a7a',
                                        'departureAirportName': '\u7f8e\u5170\u56fd\u9645\u673a\u573a',
                                        'departureTerminal': 'T2',
                                        'arrivalAirportName': '\u5929\u6cb3\u56fd\u9645\u673a\u573a',
                                        'arrivalTerminal': 'T3',
                                        'departureDateTime': '2026-05-01 12:30:00',
                                        'arrivalDateTime': '2026-05-01 15:00:00',
                                        'duration': 150,
                                        'aircraftName': '\u6ce2\u97f3737(\u4e2d)',
                                    }
                                ],
                            }
                        ],
                        'priceList': [
                            {
                                'adultPrice': 920,
                                'baggage': {'baggageTag': '\u6258\u8fd0\u884c\u674e\u989d20KG'},
                                'priceUnitList': [
                                    {
                                        'flightSeatList': [
                                            {'discountRate': 0.55, 'specialClassName': '\u8d85\u503c\u6298\u6263'}
                                        ]
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        'itineraryId': 'transfer-1',
                        'flightSegments': [
                            {
                                'segmentNo': 1,
                                'airlineName': '\u4e1c\u65b9\u822a\u7a7a',
                                'transferCount': 1,
                                'stopCount': 0,
                                'duration': 330,
                                'flightList': [
                                    {
                                        'flightNo': 'MU1111',
                                        'marketAirlineName': '\u4e1c\u65b9\u822a\u7a7a',
                                        'departureAirportName': '\u7f8e\u5170\u56fd\u9645\u673a\u573a',
                                        'departureTerminal': 'T1',
                                        'arrivalAirportName': '\u8679\u6865\u673a\u573a',
                                        'arrivalTerminal': 'T2',
                                        'arrivalCityName': '\u4e0a\u6d77',
                                        'departureDateTime': '2026-05-01 08:00:00',
                                        'arrivalDateTime': '2026-05-01 10:00:00',
                                        'duration': 120,
                                        'aircraftName': '\u7a7a\u5ba2320(\u4e2d)',
                                    },
                                    {
                                        'flightNo': 'MU2222',
                                        'marketAirlineName': '\u4e1c\u65b9\u822a\u7a7a',
                                        'departureAirportName': '\u8679\u6865\u673a\u573a',
                                        'departureTerminal': 'T2',
                                        'arrivalAirportName': '\u5929\u6cb3\u56fd\u9645\u673a\u573a',
                                        'arrivalTerminal': 'T3',
                                        'departureDateTime': '2026-05-01 11:30:00',
                                        'arrivalDateTime': '2026-05-01 13:30:00',
                                        'duration': 120,
                                        'aircraftName': '\u7a7a\u5ba2321(\u4e2d)',
                                    },
                                ],
                            }
                        ],
                        'priceList': [
                            {
                                'adultPrice': 800,
                                'baggage': {'baggageTag': '\u6258\u8fd0\u884c\u674e\u989d20KG'},
                                'priceUnitList': [
                                    {
                                        'flightSeatList': [
                                            {'discountRate': 0.48, 'specialClassName': '\u6807\u51c6\u7ecf\u6d4e\u8231'}
                                        ]
                                    }
                                ],
                            }
                        ],
                    },
                ]
            },
        }

        tickets = flight_monitor.parse_ctrip_flights(route, payload)

        self.assertEqual(len(tickets), 2)
        self.assertEqual(tickets[0].price, 800)
        self.assertEqual(tickets[0].flight_type, '\u4e2d\u8f6c')
        self.assertEqual(tickets[0].transfer_city, '\u4e0a\u6d77')
        self.assertEqual(tickets[0].transfer_duration, '1\u5c0f\u65f630\u5206\u949f')
        self.assertEqual(tickets[0].total_duration, '5\u5c0f\u65f630\u5206\u949f')
        self.assertEqual(
            tickets[0].labels[:3],
            ['4.8\u6298', '\u6258\u8fd0\u884c\u674e\u989d20KG', '\u6807\u51c6\u7ecf\u6d4e\u8231'],
        )

        self.assertEqual(tickets[1].price, 920)
        self.assertEqual(tickets[1].flight_numbers, 'HU7063')
        self.assertEqual(tickets[1].flight_type, '\u76f4\u98de')
        self.assertEqual(tickets[1].discount, '5.5\u6298')
        self.assertIn('\u6258\u8fd0\u884c\u674e\u989d20KG', tickets[1].labels)


class CtripLowestPriceParsingTests(unittest.TestCase):
    def test_build_ctrip_lowest_price_payload_uses_codes_and_date(self) -> None:
        route = {
            "departure_city_code": "BJS",
            "arrival_city_code": "SHA",
            "departure_date": "2026-04-15",
        }
        payload = flight_monitor.build_ctrip_lowest_price_payload(route)
        self.assertEqual(payload["departNewCityCode"], "BJS")
        self.assertEqual(payload["arriveNewCityCode"], "SHA")
        self.assertEqual(payload["startDate"], "2026-04-15")
        self.assertEqual(payload["searchType"], 1)

    def test_parse_ctrip_lowest_price_tickets_extracts_target_date_price(self) -> None:
        route = {
            "departure_city": "北京",
            "arrival_city": "上海",
            "departure_date": "2026-04-15",
        }
        payload = {
            "priceList": [
                {
                    "departDate": "/Date(1776182400000+0800)/",
                    "price": 520,
                    "transportPrice": 480,
                    "totalPrice": 550,
                    "directCalendarText": "直飞",
                },
                {
                    "departDate": "/Date(1776268800000+0800)/",
                    "price": 530,
                    "transportPrice": 500,
                    "totalPrice": 570,
                }
            ]
        }

        tickets = flight_monitor.parse_ctrip_lowest_price_tickets(route, payload)

        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].price, 480)
        self.assertEqual(tickets[0].flight_type, "日历最低价")
        self.assertEqual(tickets[0].airlines, "携程日历价")
        self.assertEqual(tickets[0].flight_numbers, "LOWEST-PRICE")
        self.assertIn("日历最低价", tickets[0].labels)
        self.assertIn("含税约￥550", tickets[0].labels)


class CtripTicketResolutionTests(unittest.TestCase):
    @patch("flight_monitor.safe_output")
    def test_resolve_ctrip_tickets_falls_back_to_dom_when_api_is_empty(self, safe_output_mock) -> None:
        route = {
            "departure_city": "BEIJING",
            "arrival_city": "SHANGHAI",
            "departure_date": "2026-04-15",
        }
        payload = {
            "status": 0,
            "data": {
                "flightItineraryList": [],
            },
        }
        display_rows = [
            {
                "price": "512",
                "airlines": "CHINA EASTERN",
                "flight_numbers": "MU5101",
                "departure_time": "07:30",
                "arrival_time": "09:45",
                "departure_airport": "PEK T2",
                "arrival_airport": "SHA T2",
                "range_text": "2h15m",
                "arrival_day_note": "",
                "transfer_city": "",
                "transfer_duration": "",
                "discount": "4.8x",
                "labels": ["PROMO"],
                "flight_type": "DIRECT",
            }
        ]

        tickets, parser_mode = flight_monitor.resolve_ctrip_tickets(route, payload, display_rows)

        self.assertEqual(parser_mode, "dom")
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].price, 512)
        self.assertEqual(tickets[0].flight_numbers, "MU5101")
        self.assertEqual(tickets[0].departure_airport, "PEK T2")
        self.assertEqual(tickets[0].arrival_airport, "SHA T2")
        safe_output_mock.assert_called_once()


if __name__ == '__main__':
    unittest.main()
