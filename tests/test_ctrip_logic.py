from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import flight_monitor


class CtripRouteParsingTests(unittest.TestCase):
    def test_parse_ctrip_url_extracts_codes_and_date(self) -> None:
        route = flight_monitor.parse_ctrip_url(
            'https://flights.ctrip.com/online/list/oneway-hak-wuh?depdate=2026-05-01&adult=1'
        )
        self.assertEqual(route['departure_city_code'], 'HAK')
        self.assertEqual(route['arrival_city_code'], 'WUH')
        self.assertEqual(route['departure_date'], '2026-05-01')

    def test_load_routes_from_url_file_merges_route_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            url_file = Path(tmpdir) / 'url.txt'
            url_file.write_text(
                '\u51fa\u53d1\uff1a\u6d77\u53e3 \u5230\u8fbe\uff1a\u6b66\u6c49 \u51fa\u53d1\u65e5\u671f\uff1a5\u67081\u65e5 https://flights.ctrip.com/online/list/oneway-hak-wuh?depdate=2026-05-01&adult=1\n',
                encoding='utf-8',
            )
            config = {
                'url_file': str(url_file),
                'routes': [
                    {
                        'departure_city': '\u6d77\u53e3',
                        'arrival_city': '\u6b66\u6c49',
                        'departure_date': '2026-05-01',
                        'expected_price': 950,
                        'enabled': False,
                    }
                ],
            }
            routes = flight_monitor.load_routes_from_url_file(config)
            self.assertEqual(len(routes), 1)
            self.assertEqual(routes[0]['expected_price'], 950)
            self.assertFalse(routes[0]['enabled'])
            self.assertEqual(routes[0]['departure_city'], '\u6d77\u53e3')
            self.assertEqual(routes[0]['arrival_city'], '\u6b66\u6c49')


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


if __name__ == '__main__':
    unittest.main()
