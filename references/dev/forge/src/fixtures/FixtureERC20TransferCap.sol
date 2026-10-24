// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// Test data for the detector's blind spot: the same function list as a standard ERC20, but a
/// per-transfer cap inside the code. Matching function lists alone cannot tell it apart.
contract FixtureERC20TransferCap is ERC20 {
    uint256 private constant MAX_TRANSFER = 1_000e18;

    error AboveCap(uint256 value);

    constructor() ERC20("Fixture Token", "FIX") {}

    function _update(address from, address to, uint256 value) internal override {
        if (from != address(0) && value > MAX_TRANSFER) revert AboveCap(value);
        super._update(from, to, value);
    }
}
