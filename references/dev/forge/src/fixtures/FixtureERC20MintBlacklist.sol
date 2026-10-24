// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// Test data for the detector: a standard ERC20 plus exactly two extras, an admin-only `mint` and
/// an admin-only `blacklist` that blocks transfers out of chosen addresses.
contract FixtureERC20MintBlacklist is ERC20 {
    address private immutable _admin;
    mapping(address account => bool) private _blocked;

    error NotAdmin();
    error Blocked(address account);

    constructor() ERC20("Fixture Token", "FIX") {
        _admin = msg.sender;
    }

    function mint(address to, uint256 value) external {
        if (msg.sender != _admin) revert NotAdmin();
        _mint(to, value);
    }

    function blacklist(address account, bool status) external {
        if (msg.sender != _admin) revert NotAdmin();
        _blocked[account] = status;
    }

    function _update(address from, address to, uint256 value) internal override {
        if (_blocked[from]) revert Blocked(from);
        super._update(from, to, value);
    }
}
